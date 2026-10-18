"""账户：现金簿 + 持仓 + 在途资金（settlement）。

现金语义（这是整个原型的核心不变量）
  cash_total[ccy]      账面现金（含已卖出未交收的钱）
  margin_frozen[ccy]   外汇保证金占用
  order_frozen[ccy]    买入挂单冻结
  available(ccy)       = total - margin_frozen - order_frozen        ← 提现/跨市场换汇可用
  pending[(ccy,mkt)]   该市场卖出但未交收的钱（已含在 total 里）      ← 同市场可再买
  buying_power(market) = available + pending[(ccy, market)]           ← 本市场买入额度

于是矩阵里"卖出资金提现 T+1/T+2，但同市场可即时再买"这两条同时成立：
pending 计入 buying_power、不计入 available。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from ..core.calendar import add_business_days
from ..core.enums import Market, SettlementKind, Side
from ..core.models import (
    AccountSnapshot,
    BalancesOut,
    PendingOut,
    Position,
    Trade,
)
from ..core.symbols import SymbolSpec

ZERO = Decimal("0")
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class SettlementItem:
    """一笔在途资金：卖出成交后生成，到 release_at 才转入 available。"""

    id: str
    amount: Decimal
    currency: str
    market: Market
    trade_id: str
    release_at: datetime
    created_at: datetime


@dataclass
class PositionState:
    symbol: str
    qty: Decimal = ZERO  # 外汇可为负（空头）；股票不允许
    avg_cost: Decimal = ZERO
    margin: Decimal = ZERO  # 仅外汇使用
    realized_pnl: Decimal = ZERO


@dataclass
class FillResult:
    realized_pnl: Decimal = ZERO
    cash_delta: Decimal = ZERO
    margin_delta: Decimal = ZERO
    pending_entry: SettlementItem | None = None


def price_pnl(spec: SymbolSpec, qty: Decimal, entry: Decimal, exit_: Decimal, direction: int) -> Decimal:
    """价差盈亏（结算币种）。direction=+1 多头 / -1 空头。"""
    units = spec.units(qty)
    raw = units * (exit_ - entry) * direction
    if spec.market is Market.FX and spec.quote_currency != spec.settlement_currency:
        # 如 USDJPY：盈亏以 JPY 计，按平仓价折回 USD
        return raw / exit_
    return raw


class Account:
    def __init__(self, initial_cash: dict[str, Decimal | str | int]) -> None:
        self.cash_total: dict[str, Decimal] = {c.upper(): Decimal(str(v)) for c, v in initial_cash.items()}
        self.margin_frozen: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.order_frozen: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.pending: dict[tuple[str, Market], Decimal] = defaultdict(lambda: ZERO)
        self.settlements: list[SettlementItem] = []
        self.positions: dict[str, PositionState] = {}
        self.frozen_qty: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.realized_pnl: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.fees_paid: dict[str, Decimal] = defaultdict(lambda: ZERO)

    # ---- 现金视图 -------------------------------------------------------
    def credit(self, currency: str, amount: Decimal) -> None:
        """外部入金（借贷放款、生活费反哺等）。"""
        ccy = currency.upper()
        self.cash_total[ccy] = self.total(ccy) + amount

    def debit(self, currency: str, amount: Decimal) -> None:
        """外部出金（提现付房租等）。调用方保证不超过 available。"""
        ccy = currency.upper()
        self.cash_total[ccy] = self.total(ccy) - amount

    def total(self, currency: str) -> Decimal:
        return self.cash_total.get(currency.upper(), ZERO)

    def available(self, currency: str) -> Decimal:
        """真正可自由支配的钱：total 扣除冻结，再扣除该币种的全部在途资金。

        在途资金（pending）已经计入 total，但不属于 available —— 这正是
        "卖出资金同市场可再买、跨市场/提现要等交收"的落点。
        """
        ccy = currency.upper()
        return self.total(ccy) - self.margin_frozen[ccy] - self.order_frozen[ccy] - self.pending_total(ccy)

    def pending_total(self, currency: str) -> Decimal:
        ccy = currency.upper()
        return sum((v for (c, _m), v in self.pending.items() if c == ccy), ZERO)

    def pending_amount(self, currency: str, market: Market) -> Decimal:
        return self.pending[(currency.upper(), market)]

    def buying_power(self, spec: SymbolSpec) -> Decimal:
        return self.available(spec.settlement_currency) + self.pending_amount(spec.settlement_currency, spec.market)

    def withdrawable(self, currency: str) -> Decimal:
        return self.available(currency)

    # ---- 冻结 -----------------------------------------------------------
    def freeze_cash(self, currency: str, amount: Decimal) -> None:
        self.order_frozen[currency.upper()] += amount

    def unfreeze_cash(self, currency: str, amount: Decimal) -> None:
        ccy = currency.upper()
        self.order_frozen[ccy] = max(ZERO, self.order_frozen[ccy] - amount)

    def frozen_sell_qty(self, symbol: str) -> Decimal:
        return self.frozen_qty[symbol]

    def sellable_qty(self, symbol: str) -> Decimal:
        pos = self.positions.get(symbol)
        held = pos.qty if pos else ZERO
        return held - self.frozen_qty[symbol]

    def freeze_qty(self, symbol: str, qty: Decimal) -> None:
        self.frozen_qty[symbol] += qty

    def unfreeze_qty(self, symbol: str, qty: Decimal) -> None:
        self.frozen_qty[symbol] = max(ZERO, self.frozen_qty[symbol] - qty)

    # ---- 成交记账 -------------------------------------------------------
    def apply_fill(
        self,
        spec: SymbolSpec,
        side: Side,
        qty: Decimal,
        price: Decimal,
        fee_total: Decimal,
        *,
        now: datetime,
        trade_id: str,
        settlement: SettlementKind,
        settlement_days: int,
    ) -> FillResult:
        """把一笔成交写进账户：持仓、现金、保证金、在途资金。"""
        result = FillResult()
        realized = self._apply_position(spec, side, qty, price, result)
        result.realized_pnl = money(realized)
        gross = money(spec.trade_value(qty, price))
        currency = spec.settlement_currency
        self.fees_paid[currency] += fee_total

        if spec.market is Market.FX:
            # 保证金模式：开仓只占用保证金，平仓才产生现金变动
            self.margin_frozen[currency] += result.margin_delta
            result.cash_delta = result.realized_pnl
            self.cash_total[currency] = self.total(currency) + result.cash_delta
            self.realized_pnl[currency] += result.realized_pnl
            return result

        if side is Side.BUY:
            cost = gross + fee_total
            self.cash_total[currency] = self.total(currency) - cost
            result.cash_delta = -cost
            # 花钱时优先消耗本市场的在途额度：否则 pending 会被重复计入购买力
            key = (currency, spec.market)
            if self.pending[key] > ZERO:
                used = min(self.pending[key], cost)
                self.pending[key] -= used
        else:
            proceeds = gross - fee_total
            self.cash_total[currency] = self.total(currency) + proceeds
            result.cash_delta = proceeds
            if settlement is SettlementKind.T_N:
                release_at = self._release_moment(now, settlement_days, spec.market)
                item = SettlementItem(
                    id=f"stl_{uuid4().hex[:12]}",
                    amount=proceeds,
                    currency=currency,
                    market=spec.market,
                    trade_id=trade_id,
                    release_at=release_at,
                    created_at=now,
                )
                self.pending[(currency, spec.market)] += proceeds
                self.settlements.append(item)
                result.pending_entry = item

        self.realized_pnl[currency] += result.realized_pnl
        return result

    def _apply_position(self, spec: SymbolSpec, side: Side, qty: Decimal, price: Decimal, result: FillResult) -> Decimal:
        pos = self.positions.get(spec.symbol)
        if pos is None:
            pos = PositionState(symbol=spec.symbol)
            self.positions[spec.symbol] = pos

        signed = qty if side is Side.BUY else -qty
        old = pos.qty
        new = old + signed
        realized = ZERO

        same_direction = old == ZERO or (old > ZERO) == (signed > ZERO)
        if same_direction:
            if old == ZERO:
                pos.avg_cost = price
            else:
                pos.avg_cost = (abs(old) * pos.avg_cost + qty * price) / abs(new)
            if spec.market is Market.FX:
                result.margin_delta += spec.required_margin(qty, price)
                pos.margin += spec.required_margin(qty, price)
        else:
            closed = min(abs(old), qty)
            direction = 1 if old > ZERO else -1
            realized = price_pnl(spec, closed, pos.avg_cost, price, direction)
            if spec.market is Market.FX:
                released = pos.margin * closed / abs(old) if abs(old) > ZERO else ZERO
                pos.margin -= released
                result.margin_delta -= released
                remaining = qty - closed
                if remaining > ZERO:
                    # 反手：剩余量按新价开新仓
                    pos.avg_cost = price
                    result.margin_delta += spec.required_margin(remaining, price)
                    pos.margin += spec.required_margin(remaining, price)

        pos.qty = new
        if new == ZERO:
            pos.avg_cost = ZERO
            pos.margin = ZERO
            # 平掉的持仓不再出现在持仓表里
            self.positions.pop(spec.symbol, None)
        else:
            pos.realized_pnl += money(realized)
        return money(realized)

    @staticmethod
    def _release_moment(now: datetime, days: int, market: Market) -> datetime:
        trade_day: date = now.date()
        release_day = add_business_days(trade_day, days, market.value.lower())
        return datetime.combine(release_day, time(0, 0), tzinfo=timezone.utc)

    # ---- 交收释放 -------------------------------------------------------
    def release_due(self, now: datetime) -> list[SettlementItem]:
        released: list[SettlementItem] = []
        keep: list[SettlementItem] = []
        for item in self.settlements:
            if item.release_at <= now:
                key = (item.currency, item.market)
                self.pending[key] = max(ZERO, self.pending[key] - item.amount)
                released.append(item)
            else:
                keep.append(item)
        self.settlements = keep
        return released

    # ---- 快照 -----------------------------------------------------------
    def snapshot(self, prices: dict[str, Decimal], symbols_lookup) -> AccountSnapshot:
        positions: list[Position] = []
        equity: dict[str, Decimal] = defaultdict(lambda: ZERO)

        for ccy in self.cash_total:
            equity[ccy] += self.total(ccy)

        for state in self.positions.values():
            spec = symbols_lookup(state.symbol)
            last = prices.get(state.symbol)
            market_value = spec.trade_value(state.qty, last) if last is not None else None
            unrealized = None
            if last is not None and state.qty != ZERO:
                direction = 1 if state.qty > ZERO else -1
                unrealized = money(price_pnl(spec, abs(state.qty), state.avg_cost, last, direction))
            if last is not None and spec.market is not Market.FX:
                equity[spec.settlement_currency] += money(market_value)
            if unrealized is not None and spec.market is Market.FX:
                equity[spec.settlement_currency] += unrealized
            positions.append(
                Position(
                    symbol=state.symbol,
                    market=spec.market,
                    qty=state.qty,
                    avg_cost=spec.normalize_price(state.avg_cost),
                    currency=spec.settlement_currency,
                    last_price=last,
                    market_value=money(market_value) if market_value is not None else None,
                    unrealized_pnl=unrealized,
                )
            )

        currencies = sorted(set(self.cash_total))
        balances = {
            ccy: BalancesOut(
                available=money(self.available(ccy)),
                pending=money(self.pending_total(ccy)),
                frozen=money(self.margin_frozen[ccy] + self.order_frozen[ccy]),
                total=money(self.total(ccy)),
            )
            for ccy in currencies
        }

        # buying_power / withdrawable 按市场输出（同币种市场的 withdrawable 相同）
        from ..core.symbols import symbols_for

        market_ccy = {}
        for m in Market:
            specs = symbols_for(m)
            if specs:
                market_ccy[m.value] = specs[0].settlement_currency
        buying_power = {}
        withdrawable = {}
        for m, ccy in market_ccy.items():
            market_enum = Market(m)
            buying_power[m] = money(
                self.available(ccy) + self.pending_amount(ccy, market_enum)
            )
            withdrawable[m] = money(self.available(ccy))

        pending_items = [
            PendingOut(
                market=i.market,
                currency=i.currency,
                amount=money(i.amount),
                trade_id=i.trade_id,
                release_at=i.release_at,
            )
            for i in sorted(self.settlements, key=lambda x: x.release_at)
        ]

        return AccountSnapshot(
            balances=balances,
            buying_power=buying_power,
            withdrawable=withdrawable,
            positions=positions,
            equity={k: money(v) for k, v in equity.items()},
            realized_pnl={k: money(v) for k, v in self.realized_pnl.items()},
            fees_paid={k: money(v) for k, v in self.fees_paid.items()},
            pending_items=pending_items,
        )
