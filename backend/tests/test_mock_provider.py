"""MockProvider：零网络、零 key、三市场全覆盖。"""

from __future__ import annotations

import asyncio
import inspect
from decimal import Decimal

import pytest

from app.core.enums import Market
from app.providers import mock as mock_module
from app.providers.mock import MockProvider


@pytest.mark.parametrize(
    ("symbol", "market"),
    [("EURUSD", Market.FX), ("AAPL", Market.US), ("00700.HK", Market.HK)],
)
async def test_quote_shape_per_market(symbol: str, market: Market) -> None:
    provider = MockProvider(seed=7)
    quote = await provider.get_quote(symbol)
    assert quote.symbol == symbol
    assert quote.market is market
    assert quote.bid < quote.mid < quote.ask
    assert quote.stale is False
    assert quote.source == "mock"
    await provider.close()


async def test_mid_is_between_bid_and_ask_for_fx_precision() -> None:
    provider = MockProvider(seed=7)
    quote = await provider.get_quote("EURUSD")
    # 外汇 5 位小数：点差 0.00008 → 半档 0.00004
    assert quote.ask - quote.mid == Decimal("0.00004")
    assert quote.mid - quote.bid == Decimal("0.00004")
    await provider.close()


async def test_history_is_aligned_and_deterministic() -> None:
    first = MockProvider(seed=11)
    second = MockProvider(seed=11)
    bars_a = await first.get_history("NVDA", "5m", 30)
    bars_b = await second.get_history("NVDA", "5m", 30)

    assert len(bars_a) == 30
    gaps = {(bars_a[i + 1].open_time - bars_a[i].open_time).total_seconds() for i in range(len(bars_a) - 1)}
    assert gaps == {300.0}
    # 同 seed 下价格序列可复现（不含时间戳）
    assert [b.close for b in bars_a] == [b.close for b in bars_b]

    await first.close()
    await second.close()


async def test_history_ohlc_is_consistent() -> None:
    provider = MockProvider(seed=3)
    bars = await provider.get_history("AAPL", "1m", 20)
    for bar in bars:
        assert bar.low <= min(bar.open, bar.close)
        assert bar.high >= max(bar.open, bar.close)
    await provider.close()


async def test_stream_emits_ticks_for_subscribed_symbol() -> None:
    provider = MockProvider(seed=3, tick_interval=0.02)
    stream = provider.subscribe(["EURUSD"])
    try:
        tick = await asyncio.wait_for(stream.__anext__(), timeout=3)
        assert tick.symbol == "EURUSD"
        assert tick.stale is False
    finally:
        await stream.aclose()
        await provider.close()


def test_mock_provider_never_imports_network_libraries() -> None:
    source = inspect.getsource(mock_module)
    for banned in ("import httpx", "import requests", "aiohttp", "websockets"):
        assert banned not in source, f"MockProvider 不应依赖 {banned}"
