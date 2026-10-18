"""LifeEngine：把交易账户变成"带着生存压力的一局游戏"。

职责
- 游戏时间（GameClock，受 TIME_SCALE 加速）按天推进；每 7 天结算一次
  房租（¥35,000）+ 伙食（¥15,000），贷款每天计息（利滚利）。
- 现金撑不住时：先提现交易账户，再强制平仓，仍不够就是天台结局。
- 保证金率低于维持线：强制平仓并打击心态。
- 心态：真实亏损/强平扣分，跌破阈值后禁止开仓（路由层也校验）。
- 汇率穿透：欠款（JPY）按当前真实 USDJPY 折算成 USD。
- 离线暂停：没有玩家在线时 GameClock 冻结、APScheduler 挂起，断线期间不计费。

这里绝不修改任何报价/K 线：行情是"不可控的现实"，本模块只消费它的价格。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..core.enums import ErrorCode, Market, OrderType, Side, TradingError
from ..core.models import OrderRequest
from ..core.symbols import get_spec
from ..engine.account import Account
from ..engine.events import DomainEvent, TradeEvent
from ..engine.trading_engine import TradingEngine
from ..settings import Settings
from .clock import GameClock, crossed_weekend, game_date
from .events import GameOverEvent, LifeEvent
from .models import (
    EndingKind,
    LifeOut,
    LifeState,
    LifeStatus,
    Loan,
    LoanOut,
    LoanStatus,
    SettlementRecord,
    caution_level,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _clamp_sanity(value: Decimal, ceiling: Decimal) -> Decimal:
    return min(ceiling, max(ZERO, value))


class LifeEngine:
    def __init__(
        self,
        *,
        account: Account,
        engine: TradingEngine,
        clock: GameClock,
        settings: Settings,
        usdjpy_provider=None,
    ) -> None:
        self.account = account
        self.engine = engine
        self.clock = clock
        self.settings = settings
        self.usdjpy_provider = usdjpy_provider
        self._usdjpy = settings.usdjpy_fallback

        today = game_date(clock.now())
        self.state = LifeState(
            game_date=today,
            last_settled_on=today,
            cash_jpy=settings.life_initial_cash_jpy,
            rent_jpy=settings.life_rent_jpy,
            food_jpy=settings.life_food_jpy,
            sanity=settings.sanity_max,
            next_settle_on=today + timedelta(days=settings.life_settle_days),
        )
        self.settlements: list[SettlementRecord] = []
        self._lock = asyncio.Lock()
        self._pending: list[DomainEvent] = []
        self._week_interest = ZERO
        self._ws_clients = 0
        self._scheduler = None
        self._resume_info: dict | None = None

    # ------------------------------------------------------------------
    # 离线/在线切换
    # ------------------------------------------------------------------
    def attach_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def start_offline(self) -> None:
        """服务刚启动、还没有玩家在线：游戏时间默认暂停。"""
        self.clock.pause()
        if self._scheduler is not None:
            self._scheduler.pause()

    def on_client_connected(self) -> None:
        """第一个玩家连上：游戏时间恢复流动。"""
        first = self._ws_clients == 0
        self._ws_clients += 1
        if not first:
            return
        paused_at = self.clock.paused_at
        offline = self.clock.resume()
        if paused_at is not None and offline > timedelta(0):
            crossed = crossed_weekend(paused_at, paused_at + offline)
            self._resume_info = {
                "offline_seconds": int(offline.total_seconds()),
                "crossed_weekend": crossed,
                "message": "市场已休市，获得了短暂的平静（时间不计费）" if crossed else None,
            }
        if self._scheduler is not None:
            self._scheduler.resume()

    def on_client_disconnected(self) -> None:
        """最后一个玩家断开：冻结游戏时间与定时结算。"""
        self._ws_clients = max(0, self._ws_clients - 1)
        if self._ws_clients > 0:
            return
        self.clock.pause()
        if self._scheduler is not None:
            self._scheduler.pause()

    def consume_resume_info(self) -> dict | None:
        info = self._resume_info
        self._resume_info = None
        return info

    @property
    def ws_clients(self) -> int:
        return self._ws_clients

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @property
    def sanity(self) -> Decimal:
        return self.state.sanity

    def current_usdjpy(self) -> Decimal:
        """汇率穿透的取值点：优先读真实 USDJPY 最新报价，回退到配置值。"""
        if self.usdjpy_provider is not None:
            try:
                rate = self.usdjpy_provider()
            except Exception:  # noqa: BLE001 - 行情缺失时退回配置值
                rate = None
            if rate is not None and rate > ZERO:
                self._usdjpy = Decimal(rate)
        return self._usdjpy

    def can_open_position(self, req: OrderRequest) -> bool:
        """sanity 低于阈值时只允许减仓/平仓。"""
        if self.state.sanity >= self.settings.sanity_open_threshold:
            return True
        if not self.state.alive:
            return False
        spec = get_spec(req.symbol)
        pos = self.account.positions.get(req.symbol)
        held = pos.qty if pos else ZERO
        if req.side is Side.BUY:
            opening = held >= ZERO  # 回补空头是减仓，其余都是开/加多
        else:
            opening = held <= ZERO and spec.market is Market.FX  # 股票卖出=平仓；FX 开空=开仓
        return not opening

    def _planb_status(self, now: datetime) -> tuple[bool, int]:
        """Plan B 冷却：同一批粉丝一天只能薅一次。"""
        if self.state.planb_last_claimed_at is None:
            return True, 0
        elapsed = (now - self.state.planb_last_claimed_at).total_seconds()
        remaining = int(max(0, self.settings.planb_cooldown_seconds - elapsed))
        return remaining <= 0, remaining

    async def claim_planb(self, uid: str | None = None) -> dict:
        """B站求关注换生活费：成功 +50000 JPY，进 24 小时冷却。"""
        if not self.state.alive:
            raise TradingError(ErrorCode.GAME_OVER, "这一局已经结束了", {})
        now = self.engine.clock.now()
        ready, remaining = self._planb_status(now)
        if not ready:
            raise TradingError(
                ErrorCode.PLANB_COOLDOWN,
                "同一批粉丝还没凉透，再等等（久留美：不能一天薅两次）",
                {"remaining_seconds": remaining},
            )
        if uid is not None:
            value = uid.strip()
            if not value.isdigit() or not (1 <= len(value) <= 10):
                raise TradingError(
                    ErrorCode.PLANB_INVALID_UID,
                    "这不是有效的 B 站 UID（纯数字）",
                    {"uid": uid},
                )
        if self.settings.planb_delay_seconds > 0:
            await asyncio.sleep(self.settings.planb_delay_seconds)

        gain = self.settings.planb_reward_jpy
        self.state.cash_jpy += gain
        self.state.planb_last_claimed_at = now
        self.state.planb_claims += 1
        return {
            "ok": True,
            "gain_jpy": str(gain),
            "claims": self.state.planb_claims,
            "life": self.snapshot(),
        }

    def snapshot(self) -> LifeOut:
        usdjpy = self.current_usdjpy()
        debt_jpy = self.state.debt_jpy
        debt_usd = _money(debt_jpy / usdjpy) if usdjpy > ZERO else ZERO
        equity = self._equity_usd()
        planb_ready, planb_remaining = self._planb_status(self.engine.clock.now())
        return LifeOut(
            day=self.state.day,
            game_date=self.state.game_date,
            game_time=self.clock.now(),
            cash_jpy=_money(self.state.cash_jpy),
            rent_jpy=_money(self.state.rent_jpy),
            food_jpy=_money(self.state.food_jpy),
            weekly_cost_jpy=_money(self.state.rent_jpy + self.state.food_jpy),
            next_settle_on=self.state.next_settle_on,
            sanity=_money(self.state.sanity),
            caution_level=caution_level(self.state.sanity),
            status=self.state.status,
            ending=self.state.ending,
            ending_reason=self.state.ending_reason,
            online=self._ws_clients > 0,
            debt_jpy=_money(debt_jpy),
            usdjpy=_money(usdjpy) if usdjpy < Decimal("1000") else usdjpy,
            debt_usd=debt_usd,
            account_equity_usd=_money(equity),
            net_equity_usd=_money(equity - debt_usd),
            forced_liquidations=self.state.forced_liquidations,
            planb_ready=planb_ready,
            planb_cooldown_seconds=planb_remaining,
            planb_claims=self.state.planb_claims,
            loans=[self._loan_out(loan) for loan in self.state.loans],
        )

    # ------------------------------------------------------------------
    # 定时驱动（APScheduler 每秒调用；离线时 scheduler 被挂起）
    # ------------------------------------------------------------------
    async def on_timer(self, now: datetime | None = None) -> list[DomainEvent]:
        moment = now or self.clock.now()
        events: list[DomainEvent] = []
        async with self._lock:
            # 先补发后台累积的事件（如成交导致的心态崩溃）
            if self._pending:
                events.extend(self._pending)
                self._pending.clear()
            if not self.state.alive:
                return events

            today = game_date(moment)
            while today > self.state.game_date:
                next_day = self.state.game_date + timedelta(days=1)
                self.state.day += 1
                self.state.game_date = next_day
                # 利息每天计（利滚利），房租/伙食每周结算一次
                self._accrue_interest(next_day)

                if self.state.next_settle_on is not None and next_day >= self.state.next_settle_on:
                    events.extend(await self._settle_week(next_day, moment))
                    self.state.next_settle_on = next_day + timedelta(
                        days=self.settings.life_settle_days
                    )
                    if not self.state.alive:
                        return events

            events.extend(await self._check_margin_call(moment))
        return events

    # ------------------------------------------------------------------
    # 周结算：房租 + 伙食
    # ------------------------------------------------------------------
    async def _settle_week(self, day: date, moment: datetime) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        rent = self.state.rent_jpy
        food = self.state.food_jpy
        cash_before = self.state.cash_jpy
        interest = self._week_interest
        self._week_interest = ZERO

        self.state.cash_jpy -= rent
        self.state.cash_jpy -= food

        forced = False
        if self.state.cash_jpy < ZERO:
            deficit = -self.state.cash_jpy
            forced, liquidation_events = await self._cover_deficit(deficit)
            events.extend(liquidation_events)
            if self.state.cash_jpy < ZERO:
                self._end_game(
                    EndingKind.ROOFTOP,
                    f"第 {self.state.day} 天：交不起这一周的房租和伙食，你站在了天台上",
                )

        if self.state.alive:
            self._check_debt(day)

        # 平稳度过一周：心态极低时不恢复
        if self.state.alive and self.state.sanity >= Decimal("30"):
            self.state.sanity = _clamp_sanity(
                self.state.sanity + Decimal("5"), self.settings.sanity_max
            )

        ending = self.state.ending
        self.settlements.append(
            SettlementRecord(
                game_date=day,
                rent=rent,
                food=food,
                interest=self._week_interest,
                cash_before=cash_before,
                cash_after=self.state.cash_jpy,
                forced_liquidation=forced,
                ending=ending,
            )
        )
        if len(self.settlements) > 52:
            self.settlements = self.settlements[-52:]

        events.append(LifeEvent(moment, self.snapshot()))
        if ending is not None:
            events.append(GameOverEvent(moment, ending, self.state.ending_reason or "", self.state.day))
        return events

    async def force_settle(self) -> list[DomainEvent]:
        """调试/演示：立即结算一周房租（跳过等待 7 个游戏日）。"""
        async with self._lock:
            if not self.state.alive:
                return []
            moment = self.clock.now()
            self.state.day += 1
            self.state.game_date = self.state.game_date + timedelta(days=1)
            events = await self._settle_week(self.state.game_date, moment)
            self.state.next_settle_on = self.state.game_date + timedelta(
                days=self.settings.life_settle_days
            )
            return events

    def _accrue_interest(self, day: date) -> Decimal:
        total = ZERO
        for loan in self.state.loans:
            if loan.status is not LoanStatus.ACTIVE:
                continue
            interest = (loan.balance_jpy * loan.daily_rate).quantize(CENT)
            loan.balance_jpy += interest
            loan.accrued_interest_jpy += interest
            loan.last_accrued_on = day
            total += interest
        self._week_interest += total
        return total

    def _check_debt(self, day: date) -> None:
        """利滚利违约：只有"逾期且滚到还不起"才结束，刚借出来不算。"""
        overdue = [
            loan
            for loan in self.state.loans
            if loan.status is LoanStatus.ACTIVE and loan.due_date is not None and loan.due_date < day
        ]
        if not overdue:
            return
        debt = self.state.debt_jpy
        usdjpy = self.current_usdjpy()
        limit = self._equity_usd() * usdjpy * 2 + self.state.cash_jpy * Decimal("0.5")
        if debt > max(limit, ZERO):
            self._end_game(
                EndingKind.DEBT_COLLAPSE,
                f"第 {self.state.day} 天：欠款 {_money(debt)} JPY 已经滚到还不起，黑市的人找上门了",
            )

    # ------------------------------------------------------------------
    # 生活费不足：提现 → 强平 → 仍不足则天台
    # ------------------------------------------------------------------
    async def _cover_deficit(self, need: Decimal) -> tuple[bool, list[DomainEvent]]:
        events: list[DomainEvent] = []
        rate = self.current_usdjpy()
        if self._withdraw_to_cash(need, rate):
            return False, events

        # 现金不够，先按市价平掉所有持仓
        liquidation_events, _ = await self._force_liquidate_all()
        events.extend(liquidation_events)

        if self._withdraw_to_cash(need, rate):
            return True, events
        # 能提多少提多少，剩下的交给天台
        self._withdraw_to_cash(self._withdrawable_jpy(rate), rate)
        return True, events

    def _withdrawable_jpy(self, rate: Decimal) -> Decimal:
        usd = self.account.available("USD")
        hkd = self.account.available("HKD")
        if usd > ZERO and rate > ZERO:
            usd_jpy = usd * rate
        else:
            usd_jpy = ZERO
        hkd_jpy = hkd * (rate / self.settings.usdhk_rate) if hkd > ZERO and rate > ZERO else ZERO
        return usd_jpy + hkd_jpy

    def _withdraw_to_cash(self, need: Decimal, rate: Decimal) -> bool:
        """把交易账户的可用现金换成 JPY 生活费；返回是否已覆盖 need。"""
        if need <= ZERO:
            return True
        if rate <= ZERO:
            return False
        remaining = need
        usd_avail = self.account.available("USD")
        if usd_avail > ZERO:
            take_usd = min(usd_avail, remaining / rate)
            self.account.debit("USD", take_usd)
            self.state.cash_jpy += take_usd * rate
            remaining -= take_usd * rate
        if remaining > ZERO:
            hkd_per_jpy = rate / self.settings.usdhk_rate
            if hkd_per_jpy > ZERO:
                hkd_avail = self.account.available("HKD")
                take_hkd = min(hkd_avail, remaining / hkd_per_jpy)
                if take_hkd > ZERO:
                    self.account.debit("HKD", take_hkd)
                    self.state.cash_jpy += take_hkd * hkd_per_jpy
                    remaining -= take_hkd * hkd_per_jpy
        return remaining <= ZERO

    async def _force_liquidate_all(self) -> tuple[list[DomainEvent], bool]:
        events: list[DomainEvent] = []
        liquidated = False
        for symbol, pos in list(self.account.positions.items()):
            qty = pos.qty
            if qty == ZERO:
                continue
            side = Side.SELL if qty > ZERO else Side.BUY
            try:
                _order, order_events = await self.engine.place_order(
                    OrderRequest(symbol=symbol, side=side, type=OrderType.MARKET, qty=abs(qty))
                )
                events.extend(order_events)
                liquidated = True
            except TradingError:
                continue
        if liquidated:
            self.state.sanity = _clamp_sanity(
                self.state.sanity - Decimal("40"), self.settings.sanity_max
            )
            self.state.forced_liquidations += 1
        return events, liquidated

    async def _check_margin_call(self, moment: datetime) -> list[DomainEvent]:
        margin = sum(self.account.margin_frozen.values(), ZERO)
        if margin <= ZERO:
            return []
        equity = self._equity_usd()
        if equity / margin >= self.settings.maintenance_margin_ratio:
            return []
        events, liquidated = await self._force_liquidate_all()
        if liquidated:
            events.append(LifeEvent(moment, self.snapshot()))
        return events

    # ------------------------------------------------------------------
    # 心态
    # ------------------------------------------------------------------
    async def on_event(self, event: DomainEvent) -> None:
        """挂在 MarketHub 的事件流上：成交即结算心态。"""
        if isinstance(event, TradeEvent):
            self._apply_trade_sanity(event.trade)

    def _apply_trade_sanity(self, trade) -> None:
        pnl = trade.realized_pnl
        if pnl is None or pnl == ZERO:
            return  # 开仓成交没有已实现盈亏，不影响心态
        equity = max(self._equity_usd(), Decimal("1"))
        if pnl < ZERO:
            self.state.consecutive_losses += 1
            severity = abs(pnl) / equity
            if severity >= Decimal("0.3"):
                self.state.sanity -= Decimal("25")
            elif severity >= Decimal("0.1"):
                self.state.sanity -= Decimal("10")
            else:
                self.state.sanity -= Decimal("2")
            if self.state.consecutive_losses >= 3:
                self.state.sanity -= Decimal("5")
                self.state.consecutive_losses = 0
        else:
            self.state.consecutive_losses = 0
            self.state.sanity += Decimal("2")
        self.state.sanity = _clamp_sanity(self.state.sanity, self.settings.sanity_max)
        if self.state.sanity <= ZERO and self.state.alive:
            self._end_game(EndingKind.SANITY_CRASH, "第 %d 天：你已经分不清 K 线和现实" % self.state.day)
            self._pending.append(
                GameOverEvent(
                    self.clock.now(),
                    EndingKind.SANITY_CRASH,
                    self.state.ending_reason or "",
                    self.state.day,
                )
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _equity_usd(self) -> Decimal:
        snap = self.engine.snapshot()
        usd = snap.equity.get("USD", ZERO)
        hkd = snap.equity.get("HKD", ZERO)
        return usd + hkd / self.settings.usdhk_rate

    def _end_game(self, ending: EndingKind, reason: str) -> None:
        if not self.state.alive:
            return
        self.state.status = LifeStatus.GAME_OVER
        self.state.ending = ending
        self.state.ending_reason = reason

    def _loan_out(self, loan: Loan) -> LoanOut:
        return LoanOut(
            id=loan.id,
            lender=loan.lender,
            principal_jpy=_money(loan.principal_jpy),
            balance_jpy=_money(loan.balance_jpy),
            daily_rate=loan.daily_rate,
            accrued_interest_jpy=_money(loan.accrued_interest_jpy),
            taken_on=loan.taken_on,
            due_date=loan.due_date,
            status=loan.status,
        )

    async def reset(self) -> None:
        async with self._lock:
            today = game_date(self.clock.now())
            self.state = LifeState(
                game_date=today,
                last_settled_on=today,
                cash_jpy=self.settings.life_initial_cash_jpy,
                rent_jpy=self.settings.life_rent_jpy,
                food_jpy=self.settings.life_food_jpy,
                sanity=self.settings.sanity_max,
                next_settle_on=today + timedelta(days=self.settings.life_settle_days),
            )
            self.settlements.clear()
            self._pending.clear()
            self._resume_info = None
