"""挂单簿与触价判定。

撮合规则（原型：全额成交，不做部分成交）
  限价买：ask <= limit 时成交，成交价取 ask（对手价，不会优于限价）
  限价卖：bid >= limit 时成交，成交价取 bid
  市价单：由引擎直接以 ask/bid 成交，不进挂单簿
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ..core.enums import OrderStatus, Side
from ..core.models import Order, Quote


class OrderBook:
    def __init__(self) -> None:
        self._by_symbol: dict[str, list[Order]] = defaultdict(list)

    def add(self, order: Order) -> None:
        self._by_symbol[order.symbol].append(order)

    def remove(self, order: Order) -> None:
        bucket = self._by_symbol.get(order.symbol)
        if not bucket:
            return
        self._by_symbol[order.symbol] = [o for o in bucket if o.id != order.id]
        if not self._by_symbol[order.symbol]:
            self._by_symbol.pop(order.symbol, None)

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        if symbol is None:
            return [o for bucket in self._by_symbol.values() for o in bucket if o.status is OrderStatus.PENDING]
        return [o for o in self._by_symbol.get(symbol, ()) if o.status is OrderStatus.PENDING]

    def symbols(self) -> list[str]:
        return list(self._by_symbol.keys())


def trigger_price(order: Order, quote: Quote) -> Decimal | None:
    """返回该挂单的成交价；不触价返回 None。"""
    if order.limit_price is None:
        return None
    if order.side is Side.BUY:
        return quote.ask if quote.ask <= order.limit_price else None
    return quote.bid if quote.bid >= order.limit_price else None
