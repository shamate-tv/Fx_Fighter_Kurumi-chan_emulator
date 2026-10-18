"""跨市场语义：同市场即时再投资、跨市场必须等交收（或显式换汇）。"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.enums import ErrorCode, OrderStatus, Side, TradingError
from tests.helpers import feed, make_order

# 港股一手（100 股 @ 380.10）的成本：成交额 38010 + 四项费用 43.78
HK_LOT_COST = Decimal("38053.78")


async def _hk_roundtrip(engine) -> None:
    await feed(engine, "00700.HK", mid=Decimal("380.00"))
    await engine.place_order(make_order("00700.HK", Side.BUY, "100"))
    await engine.place_order(make_order("00700.HK", Side.SELL, "100"))


async def test_pending_powers_same_market_purchase(tight_engine) -> None:
    """港股卖出所得即时可用于买港股，尽管可用现金只剩 1 万多。"""
    await _hk_roundtrip(tight_engine)

    snapshot = tight_engine.snapshot()
    assert snapshot.balances["HKD"].pending > Decimal("0")
    # 纯现金口径不够买第二手
    assert snapshot.balances["HKD"].available < HK_LOT_COST
    # 但加上在途额度就够了
    assert snapshot.buying_power["HK"] >= HK_LOT_COST

    second, _ = await tight_engine.place_order(make_order("00700.HK", Side.BUY, "100"))
    assert second.status is OrderStatus.FILLED


async def test_pending_does_not_cross_markets(tight_engine) -> None:
    """港股在途资金不能直接变成美股购买力。"""
    await _hk_roundtrip(tight_engine)
    await feed(tight_engine, "AAPL", mid=Decimal("228.50"))

    with pytest.raises(TradingError) as excinfo:
        await tight_engine.place_order(make_order("AAPL", Side.BUY, "1"))
    assert excinfo.value.code is ErrorCode.INSUFFICIENT_BUYING_POWER


async def test_pending_cash_cannot_be_converted(tight_engine) -> None:
    """换汇只能动 available，不能动 pending。"""
    await _hk_roundtrip(tight_engine)
    total_hkd = tight_engine.account.total("HKD")
    available_hkd = tight_engine.account.available("HKD")

    # 金额大于 available 但小于 total → 必须被拒绝
    amount = available_hkd + Decimal("1000")
    assert amount < total_hkd

    with pytest.raises(TradingError) as excinfo:
        await tight_engine.convert_currency("HKD", "USD", amount)
    assert excinfo.value.code is ErrorCode.INSUFFICIENT_CASH


async def test_convert_enables_cross_market_then_buy(tight_engine) -> None:
    await _hk_roundtrip(tight_engine)
    await feed(tight_engine, "AAPL", mid=Decimal("228.50"))

    await tight_engine.convert_currency("HKD", "USD", Decimal("2000"))
    usd = tight_engine.account.available("USD")
    assert usd > Decimal("240")

    order, _ = await tight_engine.place_order(make_order("AAPL", Side.BUY, "1"))
    assert order.status is OrderStatus.FILLED


async def test_convert_same_currency_rejected(tight_engine) -> None:
    with pytest.raises(TradingError) as excinfo:
        await tight_engine.convert_currency("USD", "USD", Decimal("10"))
    assert excinfo.value.code is ErrorCode.SAME_CURRENCY


async def test_settled_cash_is_withdrawable_after_t2(engine, clock) -> None:
    """T+2 之后，港股卖出所得才转入 available（可跨市场/可提现）。"""
    await _hk_roundtrip(engine)
    available_before = engine.account.available("HKD")

    clock.advance(timedelta(days=2))
    await engine.on_timer(clock.now())

    snapshot = engine.snapshot()
    assert snapshot.balances["HKD"].pending == Decimal("0")
    assert snapshot.balances["HKD"].available == available_before + Decimal("37947.22")
