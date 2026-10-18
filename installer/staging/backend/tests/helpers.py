"""测试辅助：把一条报价灌进引擎（等价于行情到达）。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from app.core.enums import OrderType, Side
from app.core.models import OrderRequest, Quote
from app.core.symbols import get_spec
from app.engine.trading_engine import TradingEngine


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0, interval: float = 0.01) -> bool:
    """有超时的条件等待：测试里绝不允许无限等流式回调。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


def make_order(
    symbol: str,
    side: Side,
    qty: str,
    *,
    order_type: OrderType = OrderType.MARKET,
    limit: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=side,
        type=order_type,
        qty=Decimal(qty),
        limit_price=Decimal(limit) if limit is not None else None,
    )


async def feed(
    engine: TradingEngine,
    symbol: str,
    *,
    mid: Decimal | str | None = None,
    bid: Decimal | str | None = None,
    ask: Decimal | str | None = None,
    ts: datetime | None = None,
    stale: bool = False,
) -> Quote:
    spec = get_spec(symbol)
    mid_dec = Decimal(str(mid)) if mid is not None else spec.start_price
    half = spec.spread / 2
    bid_dec = spec.normalize_price(Decimal(str(bid))) if bid is not None else spec.normalize_price(mid_dec - half)
    ask_dec = spec.normalize_price(Decimal(str(ask))) if ask is not None else spec.normalize_price(mid_dec + half)
    quote = Quote(
        symbol=spec.symbol,
        market=spec.market,
        bid=bid_dec,
        ask=ask_dec,
        mid=spec.normalize_price(mid_dec),
        ts=ts or engine.clock.now(),
        stale=stale,
        source="test",
    )
    await engine.on_quote(quote)
    return quote
