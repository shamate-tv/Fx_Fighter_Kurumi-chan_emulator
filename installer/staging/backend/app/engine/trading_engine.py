"""撮合与记账引擎：账户状态的唯一写入口，所有方法在单把 asyncio.Lock 内串行执行。

职责边界
- 校验（标的 / lot_size / 限价 / 购买力 / 持仓 / 行情可得性）
- 冻结（买入现金、卖出持仓、外汇保证金）
- 成交（市价立即、限价触价）
- 记账（交 Account），产出领域事件给上层广播
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from ..core.clock import Clock
from ..core.enums import ErrorCode, Market, OrderStatus, OrderType, Side, TradingError
from ..core.fees import FeeEngine, total_fees
from ..core.market_rules import MARKET_RULES
from ..core.models import Order, OrderRequest, Quote, Trade
from ..core.symbols import SymbolSpec, get_spec
from .account import Account
from .events import AccountEvent, DomainEvent, OrderEvent, SettlementEvent, TradeEvent
from .matcher import OrderBook, trigger_price

ZERO = Decimal("0")


class TradingEngine:
    def __init__(
        self,
        account: Account,
        clock: Clock,
        fee_engine: FeeEngine | None = None,
        *,
        usdhk_rate: Decimal = Decimal("7.80"),
        conversion_spread_bps: Decimal = Decimal("25"),
    ) -> None:
        self.account = account
        self.clock = clock
        self.fee_engine = fee_engine or FeeEngine()
        self.usdhk_rate = usdhk_rate
        self.conversion_spread_bps = conversion_spread_bps
        self.book = OrderBook()
        self.orders: dict[str, Order] = {}
        self.trades: list[Trade] = []
        self.last_quotes: dict[str, Quote] = {}
        self._frozen: dict[str, tuple[str, Decimal, Decimal]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 下单
    # ------------------------------------------------------------------
    async def place_order(self, req: OrderRequest) -> tuple[Order, list[DomainEvent]]:
        async with self._lock:
            spec = get_spec(req.symbol)
            rule = MARKET_RULES[spec.market]
            spec.validate_qty(req.qty)

            limit: Decimal | None = None
            if req.type is OrderType.LIMIT:
                if req.limit_price is None:
                    raise TradingError(
                        ErrorCode.INVALID_LIMIT_PRICE, "限价单必须提供 limit_price", {"symbol": spec.symbol}
                    )
                spec.validate_price(req.limit_price)
                limit = spec.normalize_price(req.limit_price)
            elif req.limit_price is not None:
                raise TradingError(ErrorCode.INVALID_LIMIT_PRICE, "市价单不接受 limit_price")

            now = self.clock.now()
            quote = self.last_quotes.get(spec.symbol)
            if req.type is OrderType.MARKET and quote is None:
                raise TradingError(
                    ErrorCode.NO_MARKET_DATA, f"{spec.symbol} 暂无可用行情，市价单无法成交", {"symbol": spec.symbol}
                )

            ref_price = limit if limit is not None else (quote.ask if req.side is Side.BUY else quote.bid)

            frozen_cash = ZERO
            frozen_qty = ZERO
            if req.side is Side.BUY or spec.market is Market.FX:
                frozen_cash = self._required_cash(spec, req.side, req.qty, ref_price)
                if frozen_cash > ZERO and self.account.buying_power(spec) < frozen_cash:
                    raise TradingError(
                        ErrorCode.INSUFFICIENT_BUYING_POWER,
                        f"{spec.settlement_currency} 购买力不足：需要 {frozen_cash}，可用 {self.account.buying_power(spec)}",
                        {
                            "required": str(frozen_cash),
                            "buying_power": str(self.account.buying_power(spec)),
                            "currency": spec.settlement_currency,
                        },
                    )
                self.account.freeze_cash(spec.settlement_currency, frozen_cash)
            else:
                if self.account.sellable_qty(spec.symbol) < req.qty:
                    raise TradingError(
                        ErrorCode.INSUFFICIENT_POSITION,
                        f"{spec.symbol} 可卖数量不足：需要 {req.qty}，可卖 {self.account.sellable_qty(spec.symbol)}",
                        {"required": str(req.qty), "sellable": str(self.account.sellable_qty(spec.symbol))},
                    )
                frozen_qty = req.qty
                self.account.freeze_qty(spec.symbol, req.qty)

            order = Order(
                id=f"ord_{uuid4().hex[:12]}",
                symbol=spec.symbol,
                market=spec.market,
                side=req.side,
                type=req.type,
                qty=req.qty,
                limit_price=limit,
                created_at=now,
                updated_at=now,
                client_order_id=req.client_order_id,
            )
            self.orders[order.id] = order
            self._frozen[order.id] = (spec.settlement_currency, frozen_cash, frozen_qty)
            events: list[DomainEvent] = [OrderEvent(now, order)]

            if req.type is OrderType.MARKET:
                assert quote is not None
                self._fill(order, quote.ask if req.side is Side.BUY else quote.bid, now, events)
            else:
                self.book.add(order)
                if quote is not None:
                    price = trigger_price(order, quote)
                    if price is not None:
                        self._fill(order, price, now, events)

            return order, events

    async def cancel_order(self, order_id: str) -> tuple[Order, list[DomainEvent]]:
        async with self._lock:
            order = self.orders.get(order_id)
            if order is None:
                raise TradingError(ErrorCode.ORDER_NOT_FOUND, f"订单不存在：{order_id}", {"order_id": order_id})
            if order.status is not OrderStatus.PENDING:
                raise TradingError(
                    ErrorCode.ORDER_NOT_CANCELLABLE,
                    f"订单状态 {order.status.value} 不可撤销",
                    {"order_id": order_id, "status": order.status.value},
                )

            spec = get_spec(order.symbol)
            ccy, frozen_cash, frozen_qty = self._frozen.pop(order.id, (spec.settlement_currency, ZERO, ZERO))
            if frozen_cash > ZERO:
                self.account.unfreeze_cash(ccy, frozen_cash)
            if frozen_qty > ZERO:
                self.account.unfreeze_qty(spec.symbol, frozen_qty)

            self.book.remove(order)
            now = self.clock.now()
            order.status = OrderStatus.CANCELLED
            order.updated_at = now
            return order, [OrderEvent(now, order)]

    # ------------------------------------------------------------------
    # 行情驱动撮合
    # ------------------------------------------------------------------
    async def on_quote(self, quote: Quote) -> list[DomainEvent]:
        async with self._lock:
            if quote.stale:
                # 收盘/陈旧的报价只能拿去画图，不允许用来成交
                return []
            self.last_quotes[quote.symbol] = quote
            events: list[DomainEvent] = []
            for order in self.book.open_orders(quote.symbol):
                price = trigger_price(order, quote)
                if price is not None:
                    self._fill(order, price, quote.ts, events)
            return events

    # ------------------------------------------------------------------
    # 交收释放
    # ------------------------------------------------------------------
    async def on_timer(self, now: datetime | None = None) -> list[DomainEvent]:
        async with self._lock:
            moment = now or self.clock.now()
            released = self.account.release_due(moment)
            if not released:
                return []
            events: list[DomainEvent] = [SettlementEvent(moment, item) for item in released]
            events.append(AccountEvent(moment, self.snapshot()))
            return events

    # ------------------------------------------------------------------
    # 账户视图
    # ------------------------------------------------------------------
    def snapshot(self):
        prices = {sym: q.mid for sym, q in self.last_quotes.items()}
        return self.account.snapshot(prices, get_spec)

    def list_orders(self, status: str | None = None) -> list[Order]:
        items = sorted(self.orders.values(), key=lambda o: o.created_at, reverse=True)
        if status in (None, "all"):
            return items
        if status == "open":
            return [o for o in items if o.status is OrderStatus.PENDING]
        return [o for o in items if o.status.value.lower() == status.lower()]

    def list_trades(self, limit: int = 200) -> list[Trade]:
        return sorted(self.trades, key=lambda t: t.ts, reverse=True)[:limit]

    async def reset(self, initial_cash: dict[str, Decimal | str | int]) -> None:
        async with self._lock:
            self.account = Account(initial_cash)
            self.book = OrderBook()
            self.orders.clear()
            self.trades.clear()
            self._frozen.clear()

    async def convert_currency(self, from_currency: str, to_currency: str, amount: Decimal) -> list[DomainEvent]:
        """显式换汇（跨市场再投资用）：只动 available 口径的现金。"""
        async with self._lock:
            source, target = from_currency.upper(), to_currency.upper()
            if source == target:
                raise TradingError(ErrorCode.SAME_CURRENCY, "换汇的两个币种相同", {"currency": source})
            if amount <= ZERO:
                raise TradingError(ErrorCode.INVALID_QTY, "换汇金额必须为正数", {"amount": str(amount)})
            if self.account.available(source) < amount:
                raise TradingError(
                    ErrorCode.INSUFFICIENT_CASH,
                    f"{source} 可用余额不足：需要 {amount}，可用 {self.account.available(source)}",
                    {"required": str(amount), "available": str(self.account.available(source))},
                )
            rate = self._rate(source, target)
            received = (amount * rate).quantize(Decimal("0.01"))
            self.account.cash_total[source] = self.account.total(source) - amount
            self.account.cash_total[target] = self.account.total(target) + received
            return [AccountEvent(self.clock.now(), self.snapshot())]

    def _rate(self, source: str, target: str) -> Decimal:
        pair = {source, target}
        if pair != {"USD", "HKD"}:
            raise TradingError(
                ErrorCode.INVALID_QTY, f"暂不支持 {source}→{target} 的换汇", {"source": source, "target": target}
            )
        mid = self.usdhk_rate if source == "USD" else Decimal("1") / self.usdhk_rate
        spread = Decimal("1") - self.conversion_spread_bps / Decimal("10000")
        return mid * spread

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _opening_qty(self, spec: SymbolSpec, side: Side, qty: Decimal) -> Decimal:
        """本次下单里"开新仓"的数量（平仓部分不占用保证金）。"""
        pos = self.account.positions.get(spec.symbol)
        held = pos.qty if pos else ZERO
        if side is Side.BUY:
            closable = max(ZERO, -held)
            return max(ZERO, qty - closable)
        closable = max(ZERO, held)
        return max(ZERO, qty - closable)

    def _required_cash(self, spec: SymbolSpec, side: Side, qty: Decimal, price: Decimal) -> Decimal:
        """下单时需要冻结的钱：外汇是保证金，股票是成交额 + 预估费用。"""
        if spec.market is Market.FX:
            opening = self._opening_qty(spec, side, qty)
            if opening <= ZERO:
                return ZERO
            return spec.required_margin(opening, price).quantize(Decimal("0.01"))
        fees = total_fees(self.fee_engine.calculate(spec, side, qty, price))
        return (spec.trade_value(qty, price) + fees).quantize(Decimal("0.01"))

    def _fill(self, order: Order, price: Decimal, now: datetime, events: list[DomainEvent]) -> None:
        spec = get_spec(order.symbol)
        rule = MARKET_RULES[spec.market]
        price = spec.normalize_price(price)

        ccy, frozen_cash, frozen_qty = self._frozen.pop(order.id, (spec.settlement_currency, ZERO, ZERO))
        if frozen_cash > ZERO:
            self.account.unfreeze_cash(ccy, frozen_cash)
        if frozen_qty > ZERO:
            self.account.unfreeze_qty(spec.symbol, frozen_qty)

        fees = self.fee_engine.calculate(spec, order.side, order.qty, price)
        fee_total = total_fees(fees)
        trade_id = f"trd_{uuid4().hex[:12]}"
        result = self.account.apply_fill(
            spec,
            order.side,
            order.qty,
            price,
            fee_total,
            now=now,
            trade_id=trade_id,
            settlement=rule.settlement,
            settlement_days=rule.settlement_days,
        )

        trade = Trade(
            id=trade_id,
            order_id=order.id,
            symbol=spec.symbol,
            market=spec.market,
            side=order.side,
            qty=order.qty,
            price=price,
            gross=spec.trade_value(order.qty, price).quantize(Decimal("0.01")),
            fees=fees,
            fee_total=fee_total,
            currency=spec.settlement_currency,
            trade_date=now.date(),
            settles_on=result.pending_entry.release_at.date() if result.pending_entry else None,
            realized_pnl=result.realized_pnl,
            ts=now,
        )
        self.trades.append(trade)

        order.status = OrderStatus.FILLED
        order.filled_qty = order.qty
        order.avg_fill_price = price
        order.fee = fee_total
        order.updated_at = now
        self.book.remove(order)

        events.append(TradeEvent(now, trade))
        events.append(OrderEvent(now, order))
        events.append(AccountEvent(now, self.snapshot()))
