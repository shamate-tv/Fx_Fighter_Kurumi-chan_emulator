"""Alpaca 解析层与降级行为。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from app.core.symbols import SYMBOLS
from app.providers.alpaca import (
    AlpacaProvider,
    parse_alpaca_ts,
    quote_from_alpaca_payload,
)
from app.providers.mock import MockProvider

# Alpaca IEX 行情的真实消息形状（纳秒精度时间戳）
REAL_QUOTE = {
    "T": "q",
    "S": "AAPL",
    "bx": "V",
    "bp": 179.98,
    "bs": 2,
    "ax": "V",
    "ap": 180.02,
    "as": 3,
    "c": ["R"],
    "z": "C",
    "t": "2026-10-16T20:59:12.123456789Z",
}
AFTER = datetime(2026, 10, 16, 21, 0, 0, tzinfo=timezone.utc)


def test_parse_nanosecond_timestamp() -> None:
    ts = parse_alpaca_ts(REAL_QUOTE["t"])
    assert ts.microsecond == 123456
    assert ts.tzinfo is not None


def test_quote_uses_midpoint_of_bid_and_ask() -> None:
    spec = SYMBOLS["AAPL"]
    quote = quote_from_alpaca_payload(REAL_QUOTE, spec, AFTER, stale_after=300)
    assert quote is not None
    assert quote.bid == Decimal("179.98")
    assert quote.ask == Decimal("180.02")
    assert quote.mid == Decimal("180.00")
    assert quote.source == "alpaca"
    assert quote.stale is False


def test_quote_with_zero_size_is_dropped() -> None:
    spec = SYMBOLS["AAPL"]
    payload = dict(REAL_QUOTE, bp=0, ap=0)
    assert quote_from_alpaca_payload(payload, spec, AFTER) is None


def test_crossed_quote_is_dropped() -> None:
    spec = SYMBOLS["AAPL"]
    payload = dict(REAL_QUOTE, bp=181.0, ap=180.0)
    assert quote_from_alpaca_payload(payload, spec, AFTER) is None


def test_old_quote_is_stale() -> None:
    spec = SYMBOLS["AAPL"]
    much_later = datetime(2026, 10, 18, 9, 0, 0, tzinfo=timezone.utc)
    quote = quote_from_alpaca_payload(REAL_QUOTE, spec, much_later, stale_after=300)
    assert quote is not None
    assert quote.stale is True


def test_stream_url_is_iex_only() -> None:
    """必须只连 IEX 行情流，绝不能连交易流。"""
    assert AlpacaProvider.STREAM_URL == "wss://stream.data.alpaca.markets/v2/iex"
    assert "paper-api" not in AlpacaProvider.STREAM_URL


def test_unconfigured_provider_reports_disabled() -> None:
    provider = AlpacaProvider("", "")
    assert provider.configured is False
    status = provider.status()
    assert status["enabled"] is False
    assert "secret" not in json.dumps(status).lower()


async def test_unconfigured_provider_falls_back_to_mock() -> None:
    mock = MockProvider(seed=5)
    provider = AlpacaProvider("", "", fallback=mock)
    quote = await provider.get_quote("AAPL")
    assert quote.source == "mock"
    await mock.close()


async def test_unconfigured_provider_subscribe_falls_back() -> None:
    import asyncio

    mock = MockProvider(seed=5, tick_interval=0.02)
    provider = AlpacaProvider("", "", fallback=mock)
    stream = provider.subscribe(["AAPL"])
    try:
        tick = await asyncio.wait_for(stream.__anext__(), timeout=3)
        assert tick.symbol == "AAPL"
        assert tick.source == "mock"
    finally:
        await stream.aclose()
        await mock.close()


async def test_real_alpaca_quote_triggers_engine_fill(clock) -> None:
    """用真实 Alpaca IEX 报价形状，验证它能自动成交一张挂单。"""
    from app.core.enums import OrderStatus, OrderType, Side
    from app.engine.account import Account
    from app.engine.trading_engine import TradingEngine
    from tests.helpers import feed, make_order

    engine = TradingEngine(Account({"USD": Decimal("100000")}), clock)
    await feed(engine, "AAPL", mid=Decimal("228.50"))

    order, _ = await engine.place_order(
        make_order("AAPL", Side.BUY, "1", order_type=OrderType.LIMIT, limit="180.02")
    )
    assert order.status is OrderStatus.PENDING

    payload = dict(REAL_QUOTE, t=clock.now().isoformat())
    quote = quote_from_alpaca_payload(payload, SYMBOLS["AAPL"], clock.now(), stale_after=300)
    assert quote is not None and quote.stale is False
    assert quote.mid == Decimal("180.00")

    await engine.on_quote(quote)

    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("180.02")
