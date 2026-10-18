"""biquote 解析层：用真实抓到的帧做无网络测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from app.core.symbols import SYMBOLS
from app.providers.biquote import (
    bars_from_ohlc,
    quote_from_rest,
    quote_from_signalr,
)
from app.providers.signalr import decode_frame, invoke_frame, split_frames

# 2026-10-16 实测抓到的 ReceiveTick 记录（周五收盘后的重复推送）
REAL_TICK_FRAME = (
    '{"type":1,"target":"ReceiveTick","arguments":[{"symbol":"EURUSD","bid":1.12390,'
    '"ask":1.12396,"last":0.00000,"volume":0,"timestamp":"2026-10-16T20:58:43Z",'
    '"source":"MetaTrader 5 (Broker 1)","high":1.12693,"low":1.12078,"direction":"DOWN",'
    '"dayDiffPercent":0.4011,"description":"Euro vs US Dollar","time":"2026.10.16 20:58:43",'
    '"exchange":"FOREX","spread":0.00006,"mid":1.12393}]}'
)
AFTER_TICK = datetime(2026, 10, 16, 21, 0, 0, tzinfo=timezone.utc)
WEEKEND = datetime(2026, 10, 18, 9, 0, 0, tzinfo=timezone.utc)


def _payload(frame: str) -> dict:
    frames, rest = split_frames(frame + "\x1e")
    assert rest == ""
    data = decode_frame(frames[0])
    assert data is not None
    return data


# ---- 分帧 ---------------------------------------------------------------

def test_split_frames_handles_concatenated_and_partial_records() -> None:
    buffer = '{"a":1}\x1e{"b":2}\x1e{"c":'
    frames, rest = split_frames(buffer)
    assert frames == ['{"a":1}', '{"b":2}']
    assert rest == '{"c":'


def test_split_frames_without_separator_returns_nothing() -> None:
    frames, rest = split_frames('{"a":1}')
    assert frames == []
    assert rest == '{"a":1}'


def test_invoke_frame_matches_signalr_shape() -> None:
    frame = invoke_frame("Subscribe", [["EURUSD"]], "1")
    assert frame.endswith("\x1e")
    parsed = json.loads(frame.rstrip("\x1e"))
    assert parsed == {
        "type": 1,
        "invocationId": "1",
        "target": "Subscribe",
        "arguments": [["EURUSD"]],
    }


def test_decode_frame_ignores_garbage() -> None:
    assert decode_frame("not-json") is None
    assert decode_frame("") is None
    assert decode_frame("[1,2]") is None


# ---- 帧 → Quote ---------------------------------------------------------

def test_receive_tick_frame_extracts_symbol_and_mid() -> None:
    data = _payload(REAL_TICK_FRAME)
    assert data["target"] == "ReceiveTick"
    payload = data["arguments"][0]
    assert payload["symbol"] == "EURUSD"
    # 用户强调过：last/volume 恒为 0，价格只能取 mid
    assert payload["last"] == 0
    assert payload["volume"] == 0


def test_quote_from_signalr_uses_mid_not_last() -> None:
    spec = SYMBOLS["EURUSD"]
    payload = _payload(REAL_TICK_FRAME)["arguments"][0]
    quote = quote_from_signalr(payload, spec, AFTER_TICK, stale_after=180)

    assert quote is not None
    assert quote.symbol == "EURUSD"
    assert quote.mid == Decimal("1.12393")  # 就是帧里的 mid
    assert quote.bid == Decimal("1.12390")
    assert quote.ask == Decimal("1.12396")
    assert quote.source == "biquote"
    assert quote.stale is False


def test_weekend_replay_of_same_tick_is_stale() -> None:
    """外汇闭市时服务端会反复推同一条旧 tick，必须判 stale 才能挡住撮合。"""
    spec = SYMBOLS["EURUSD"]
    payload = _payload(REAL_TICK_FRAME)["arguments"][0]
    quote = quote_from_signalr(payload, spec, WEEKEND, stale_after=180)
    assert quote is not None
    assert quote.stale is True


def test_mid_missing_falls_back_to_bid_ask_midpoint() -> None:
    spec = SYMBOLS["EURUSD"]
    payload = {
        "symbol": "EURUSD",
        "bid": 1.0500,
        "ask": 1.0502,
        "mid": 0,
        "timestamp": "2026-10-16T20:58:43Z",
    }
    quote = quote_from_signalr(payload, spec, AFTER_TICK, stale_after=180)
    assert quote is not None
    assert quote.mid == Decimal("1.05010")


def test_quote_without_any_price_is_dropped() -> None:
    spec = SYMBOLS["EURUSD"]
    payload = {"symbol": "EURUSD", "bid": 0, "ask": 0, "mid": 0, "timestamp": "2026-10-16T20:58:43Z"}
    assert quote_from_signalr(payload, spec, AFTER_TICK) is None


def test_rest_payload_honours_market_state_closed() -> None:
    spec = SYMBOLS["EURUSD"]
    payload = {
        "symbol": "EURUSD",
        "bid": 1.12390,
        "ask": 1.12396,
        "mid": 1.12393,
        "timestamp": "2026-10-16T20:58:43Z",
        "stale": False,
        "marketState": "closed",
    }
    quote = quote_from_rest(payload, spec, AFTER_TICK, stale_after=180)
    assert quote is not None
    assert quote.stale is True


# ---- K 线 ---------------------------------------------------------------

def test_ohlc_bars_are_reordered_oldest_first() -> None:
    payload = {
        "symbol": "EURUSD",
        "interval": "1h",
        "bars": [
            {"openTime": "2026-10-16T20:00:00Z", "open": 1.12, "high": 1.13, "low": 1.11, "close": 1.125, "tickVolume": 10},
            {"openTime": "2026-10-16T19:00:00Z", "open": 1.11, "high": 1.12, "low": 1.10, "close": 1.12, "tickVolume": 8},
        ],
    }
    bars = bars_from_ohlc(payload, "EURUSD", "1h")
    assert len(bars) == 2
    assert bars[0].open_time < bars[1].open_time
    assert bars[0].close == Decimal("1.12")
    assert bars[1].close == Decimal("1.125")


# ---- 真实帧直接驱动撮合 ------------------------------------------------

async def test_real_biquote_frame_triggers_engine_fill(clock) -> None:
    """用实测抓到的 ReceiveTick 帧，验证它能自动成交一张挂单。"""
    from app.core.enums import OrderStatus, OrderType, Side
    from app.engine.account import Account
    from app.engine.trading_engine import TradingEngine
    from tests.helpers import feed, make_order

    engine = TradingEngine(Account({"USD": Decimal("100000")}), clock)
    await feed(engine, "EURUSD", mid=Decimal("1.1300"))

    order, _ = await engine.place_order(
        make_order("EURUSD", Side.BUY, "0.10", order_type=OrderType.LIMIT, limit="1.1250")
    )
    assert order.status is OrderStatus.PENDING

    payload = dict(_payload(REAL_TICK_FRAME)["arguments"][0])
    payload["timestamp"] = clock.now().isoformat()  # 把这一帧当作"当下"的开市实时帧
    quote = quote_from_signalr(payload, SYMBOLS["EURUSD"], clock.now(), stale_after=180)
    assert quote is not None and quote.stale is False

    await engine.on_quote(quote)

    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("1.12396")
