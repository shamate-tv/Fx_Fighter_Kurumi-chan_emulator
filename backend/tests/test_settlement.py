"""交收：外汇即时、美股 T+1、港股 T+2，以及在途资金对购买力的影响。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.clock import SimClock
from app.core.enums import Side
from app.engine.account import Account
from app.engine.trading_engine import TradingEngine
from tests.helpers import feed, make_order


async def test_fx_proceeds_land_immediately(engine) -> None:
    await feed(engine, "EURUSD", mid=Decimal("1.08500"))
    await engine.place_order(make_order("EURUSD", Side.BUY, "0.10"))
    await engine.place_order(make_order("EURUSD", Side.SELL, "0.10"))

    snapshot = engine.snapshot()
    assert snapshot.balances["USD"].pending == Decimal("0")
    # 点差成本：买 ask 1.08504 / 卖 bid 1.08496 → 10000 单位 × -0.00008 = -0.80 USD
    assert snapshot.balances["USD"].available == Decimal("99999.20")


async def test_us_sell_proceeds_pending_until_t1(engine, clock: SimClock) -> None:
    await feed(engine, "AAPL", mid=Decimal("228.50"))
    await engine.place_order(make_order("AAPL", Side.BUY, "10"))
    await engine.place_order(make_order("AAPL", Side.SELL, "10"))

    snapshot = engine.snapshot()
    assert snapshot.balances["USD"].pending == Decimal("2284.90")
    assert snapshot.buying_power["US"] == snapshot.balances["USD"].available + Decimal("2284.90")
    assert snapshot.withdrawable["US"] == snapshot.balances["USD"].available

    # 成交当日不释放
    await engine.on_timer(clock.now())
    assert engine.snapshot().balances["USD"].pending == Decimal("2284.90")

    # 下一个交易日释放
    clock.advance(timedelta(days=1))
    await engine.on_timer(clock.now())
    assert engine.snapshot().balances["USD"].pending == Decimal("0")


async def test_hk_sell_proceeds_pending_until_t2(engine, clock: SimClock) -> None:
    await feed(engine, "00700.HK", mid=Decimal("380.00"))
    await engine.place_order(make_order("00700.HK", Side.BUY, "100"))
    await engine.place_order(make_order("00700.HK", Side.SELL, "100"))

    snapshot = engine.snapshot()
    # 成交额 37,990 HKD，四项费用合计 42.78 → 在途 37,947.22
    assert snapshot.balances["HKD"].pending == Decimal("37947.22")
    assert snapshot.buying_power["HK"] == snapshot.balances["HKD"].available + Decimal("37947.22")
    assert snapshot.withdrawable["HK"] == snapshot.balances["HKD"].available

    # T+1 还不释放
    clock.advance(timedelta(days=1))
    await engine.on_timer(clock.now())
    assert engine.snapshot().balances["HKD"].pending == Decimal("37947.22")

    # T+2 释放
    clock.advance(timedelta(days=1))
    await engine.on_timer(clock.now())
    snapshot = engine.snapshot()
    assert snapshot.balances["HKD"].pending == Decimal("0")
    assert snapshot.buying_power["HK"] == snapshot.balances["HKD"].available


async def test_settlement_skips_weekend(clock: SimClock) -> None:
    friday = datetime(2026, 3, 6, 9, 30, tzinfo=timezone.utc)
    clock.set(friday)
    engine = TradingEngine(Account({"HKD": Decimal("800000")}), clock)

    await feed(engine, "00700.HK", mid=Decimal("380.00"))
    await engine.place_order(make_order("00700.HK", Side.BUY, "100"))
    await engine.place_order(make_order("00700.HK", Side.SELL, "100"))

    item = engine.account.settlements[0]
    # 周五成交 → T+2 = 下周二（跳过周六周日）
    assert item.release_at.date() == date(2026, 3, 10)


async def test_settlement_when_nothing_due_returns_no_events(engine) -> None:
    assert await engine.on_timer(engine.clock.now()) == []
