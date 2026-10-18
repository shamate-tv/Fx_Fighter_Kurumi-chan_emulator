"""撮合：市价 / 限价 / 触价 / T+0 往返 / 拒绝路径。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.enums import ErrorCode, OrderStatus, OrderType, Side, TradingError
from app.core.models import OrderRequest
from tests.helpers import feed


def _order(symbol: str, side: Side, qty: str, *, order_type: OrderType = OrderType.MARKET, limit: str | None = None):
    return OrderRequest(
        symbol=symbol,
        side=side,
        type=order_type,
        qty=Decimal(qty),
        limit_price=Decimal(limit) if limit is not None else None,
    )


async def test_market_buy_fills_at_ask(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    order, events = await engine.place_order(_order("AAPL", Side.BUY, "10"))
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("228.51")
    assert order.filled_qty == Decimal("10")
    assert any(type(e).__name__ == "TradeEvent" for e in events)


async def test_market_sell_fills_at_bid(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    await engine.place_order(_order("AAPL", Side.BUY, "10"))
    order, _ = await engine.place_order(_order("AAPL", Side.SELL, "10"))
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("228.49")


async def test_t0_intraday_roundtrip_has_no_holding_lock(engine) -> None:
    """同一标的当日买入当日卖出必须成功（T+0）。"""
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    buy, _ = await engine.place_order(_order("AAPL", Side.BUY, "10"))
    sell, _ = await engine.place_order(_order("AAPL", Side.SELL, "10"))
    assert buy.status is OrderStatus.FILLED
    assert sell.status is OrderStatus.FILLED
    positions = engine.snapshot().positions
    assert positions == []


async def test_t0_roundtrip_works_for_every_market(engine) -> None:
    await feed(engine, "EURUSD", mid=Decimal("1.08500"))
    await feed(engine, "00700.HK", mid=Decimal("380.00"))

    for symbol, qty in (("EURUSD", "0.10"), ("AAPL", "10"), ("00700.HK", "100")):
        if symbol == "AAPL":
            await feed(engine, "AAPL", mid=Decimal("228.50"))
        buy, _ = await engine.place_order(_order(symbol, Side.BUY, qty))
        sell, _ = await engine.place_order(_order(symbol, Side.SELL, qty))
        assert buy.status is OrderStatus.FILLED, symbol
        assert sell.status is OrderStatus.FILLED, symbol


async def test_limit_order_waits_then_fills_on_tick(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))  # ask 228.51
    order, _ = await engine.place_order(
        _order("AAPL", Side.BUY, "10", order_type=OrderType.LIMIT, limit="228.00")
    )
    assert order.status is OrderStatus.PENDING

    # 价格没到，不成交
    await feed(engine, "AAPL", mid=Decimal("228.20"))
    assert order.status is OrderStatus.PENDING

    # 触价：ask 227.91 <= 228.00
    await feed(engine, "AAPL", mid=Decimal("227.90"))
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("227.91")


async def test_limit_buy_immediately_fills_when_already_through(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))  # ask 228.51
    order, _ = await engine.place_order(
        _order("AAPL", Side.BUY, "10", order_type=OrderType.LIMIT, limit="228.60")
    )
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("228.51")


async def test_limit_sell_triggers_on_bid(engine) -> None:
    await feed(engine, "00700.HK", mid=Decimal("380.00"))
    await engine.place_order(_order("00700.HK", Side.BUY, "100"))
    order, _ = await engine.place_order(
        _order("00700.HK", Side.SELL, "100", order_type=OrderType.LIMIT, limit="381.00")
    )
    assert order.status is OrderStatus.PENDING
    await feed(engine, "00700.HK", mid=Decimal("381.50"))  # bid 381.40 >= 381.00
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == Decimal("381.40")


async def test_market_order_without_quote_is_rejected(engine) -> None:
    with pytest.raises(TradingError) as excinfo:
        await engine.place_order(_order("MSFT", Side.BUY, "1"))
    assert excinfo.value.code is ErrorCode.NO_MARKET_DATA


async def test_insufficient_buying_power(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    with pytest.raises(TradingError) as excinfo:
        await engine.place_order(_order("AAPL", Side.BUY, "1000"))
    assert excinfo.value.code is ErrorCode.INSUFFICIENT_BUYING_POWER


async def test_selling_more_than_held_is_rejected(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    with pytest.raises(TradingError) as excinfo:
        await engine.place_order(_order("AAPL", Side.SELL, "10"))
    assert excinfo.value.code is ErrorCode.INSUFFICIENT_POSITION


async def test_stale_tick_never_fills(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    order, _ = await engine.place_order(
        _order("AAPL", Side.BUY, "10", order_type=OrderType.LIMIT, limit="228.00")
    )
    await feed(engine, "AAPL", mid=Decimal("227.00"), stale=True)
    assert order.status is OrderStatus.PENDING
    # 陈旧报价也不许污染最新价缓存
    assert engine.last_quotes["AAPL"].mid == Decimal("228.50")


async def test_cancel_releases_frozen_cash(engine) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    order, _ = await engine.place_order(
        _order("AAPL", Side.BUY, "10", order_type=OrderType.LIMIT, limit="200.00")
    )
    frozen = engine.snapshot().balances["USD"].frozen
    assert frozen > Decimal("0")

    cancelled, _ = await engine.cancel_order(order.id)
    assert cancelled.status is OrderStatus.CANCELLED
    assert engine.snapshot().balances["USD"].frozen == Decimal("0")


async def test_hk_lot_size_enforced_at_engine_level(engine) -> None:
    await feed(engine, "00700.HK", mid=Decimal("380.00"))
    with pytest.raises(TradingError) as excinfo:
        await engine.place_order(_order("00700.HK", Side.BUY, "150"))
    assert excinfo.value.code is ErrorCode.LOT_SIZE_VIOLATION
