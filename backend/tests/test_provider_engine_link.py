"""真实 Provider 链路：Provider 推流 → MarketHub → 撮合引擎 → 挂单成交。

用 StubProvider 精确控制"哪一刻吐出哪条 tick"，因此不依赖外部网络，
但走的完全是生产路径（Router → Hub → 行情缓存 → 触价 → 成交）。
所有等待都带超时，避免流式任务把测试挂死。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

from app.core.enums import Market, OrderStatus, OrderType, Side
from app.core.models import Bar, Quote
from app.core.symbols import get_spec
from app.engine.account import Account
from app.engine.trading_engine import TradingEngine
from app.hub.market_hub import MarketHub
from app.providers.base import MarketDataProvider
from app.providers.router import ProviderRouter
from tests.helpers import make_order, wait_until


class StubProvider(MarketDataProvider):
    """行为与真实 Provider 一致：有快照接口，也有异步推流。"""

    name = "stub"

    def __init__(self, clock) -> None:
        self._clock = clock
        self._queue: asyncio.Queue[Quote] = asyncio.Queue()
        self.pushed: list[Quote] = []

    async def get_quote(self, symbol: str) -> Quote:
        spec = get_spec(symbol)
        return self._build(symbol, mid=spec.start_price, spread=spec.spread)

    async def get_history(self, symbol: str, interval: str = "1m", limit: int = 200) -> list[Bar]:
        return []

    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Quote]:
        while True:
            yield await self._queue.get()

    def push(
        self,
        symbol: str,
        *,
        mid: str,
        bid: str | None = None,
        ask: str | None = None,
        stale: bool = False,
    ) -> Quote:
        quote = self._build(symbol, mid=mid, bid=bid, ask=ask, stale=stale)
        self._queue.put_nowait(quote)
        self.pushed.append(quote)
        return quote

    def _build(
        self,
        symbol: str,
        *,
        mid,
        bid=None,
        ask=None,
        spread=None,
        stale: bool = False,
    ) -> Quote:
        spec = get_spec(symbol)
        mid_dec = Decimal(str(mid))
        half = (spread if spread is not None else spec.spread) / 2
        bid_dec = Decimal(str(bid)) if bid is not None else mid_dec - half
        ask_dec = Decimal(str(ask)) if ask is not None else mid_dec + half
        return Quote(
            symbol=spec.symbol,
            market=spec.market,
            bid=spec.normalize_price(bid_dec),
            ask=spec.normalize_price(ask_dec),
            mid=spec.normalize_price(mid_dec),
            ts=self._clock.now(),
            stale=stale,
            source="stub",
        )


async def _build(clock, cash: dict[str, Decimal]):
    engine = TradingEngine(Account(cash), clock)
    provider = StubProvider(clock)
    router = ProviderRouter({market: provider for market in Market}, fallback=provider)
    hub = MarketHub(router, engine, clock, settlement_scan_interval=3600)
    seen: list = []

    async def sink(event) -> None:
        seen.append(event)

    hub.add_sink(sink)
    await hub.start()
    return engine, provider, hub, seen


async def test_foreign_provider_tick_fills_resting_limit_order(clock) -> None:
    engine, provider, hub, seen = await _build(clock, {"USD": Decimal("100000")})
    try:
        order, _ = await engine.place_order(
            make_order("EURUSD", Side.BUY, "0.10", order_type=OrderType.LIMIT, limit="1.0501")
        )
        assert order.status is OrderStatus.PENDING

        # 真实路径：provider 吐出 mid=1.0500 的 tick
        provider.push("EURUSD", mid="1.05000", bid="1.04996", ask="1.05004")

        assert await wait_until(lambda: order.status is OrderStatus.FILLED), "外汇 tick 未触发挂单成交"
        assert order.avg_fill_price == Decimal("1.05004")
        assert any(type(e).__name__ == "QuoteEvent" for e in seen)
        assert any(type(e).__name__ == "TradeEvent" for e in seen)
    finally:
        await hub.close()


async def test_us_provider_tick_fills_resting_limit_order(clock) -> None:
    engine, provider, hub, _ = await _build(clock, {"USD": Decimal("100000")})
    try:
        order, _ = await engine.place_order(
            make_order("AAPL", Side.BUY, "1", order_type=OrderType.LIMIT, limit="180.01")
        )
        assert order.status is OrderStatus.PENDING

        provider.push("AAPL", mid="180.00", bid="179.99", ask="180.01")

        assert await wait_until(lambda: order.status is OrderStatus.FILLED), "美股 tick 未触发挂单成交"
        assert order.avg_fill_price == Decimal("180.01")
    finally:
        await hub.close()


async def test_stale_provider_tick_never_fills(clock) -> None:
    """biquote 闭市时会反复推旧 tick，必须被挡在撮合之外。"""
    engine, provider, hub, _ = await _build(clock, {"USD": Decimal("100000")})
    try:
        order, _ = await engine.place_order(
            make_order("AAPL", Side.BUY, "1", order_type=OrderType.LIMIT, limit="180.01")
        )
        assert order.status is OrderStatus.PENDING

        provider.push("AAPL", mid="180.00", bid="179.99", ask="180.01", stale=True)
        await asyncio.sleep(0.3)

        assert order.status is OrderStatus.PENDING
        # 陈旧价也不许污染撮合用价
        assert engine.last_quotes["AAPL"].mid != Decimal("180.00")
    finally:
        await hub.close()


async def test_hub_shutdown_is_clean(clock) -> None:
    """Hub 停止后不应留下未退出的任务（否则测试会挂死）。"""
    _, provider, hub, _ = await _build(clock, {"USD": Decimal("100000")})
    tasks_before = len([t for t in asyncio.all_tasks() if not t.done()])
    await hub.close()
    await asyncio.sleep(0.05)
    tasks_after = len([t for t in asyncio.all_tasks() if not t.done()])
    assert tasks_after <= tasks_before
