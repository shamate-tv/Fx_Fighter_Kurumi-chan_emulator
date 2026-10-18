"""生存系统：周结算、强平、天台结局、汇率穿透、心态拦截、离线暂停、TIME_SCALE。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.clock import SimClock
from app.core.enums import Side
from app.engine.account import Account
from app.engine.events import TradeEvent
from app.engine.trading_engine import TradingEngine
from app.game.clock import GameClock, crossed_weekend
from app.game.events import GameOverEvent, LifeEvent
from app.game.life_engine import LifeEngine
from app.game.models import EndingKind, LifeStatus, Loan, LoanStatus
from app.settings import Settings
from tests.helpers import feed, make_order

START = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)  # 周一


def make_life(
    *,
    cash_jpy: str = "100000",
    initial_cash: dict[str, str] | None = None,
    scale: float = 1.0,
    usdjpy: str = "150",
):
    clock = SimClock(START)
    cash = initial_cash or {"USD": "0", "HKD": "0"}
    account = Account(cash)
    engine = TradingEngine(account, clock)
    settings = Settings(
        initial_cash={k: Decimal(v) for k, v in cash.items()},
        life_initial_cash_jpy=Decimal(cash_jpy),
    )
    game_clock = GameClock(clock, scale)
    life = LifeEngine(
        account=account,
        engine=engine,
        clock=game_clock,
        settings=settings,
        usdjpy_provider=lambda: Decimal(usdjpy),
    )
    return clock, engine, life


def add_loan(life: LifeEngine, amount: str = "100000", rate: str = "0.05") -> Loan:
    loan = Loan(
        id="loan_test",
        lender="萌智",
        principal_jpy=Decimal(amount),
        balance_jpy=Decimal(amount),
        daily_rate=Decimal(rate),
        taken_on=date(2026, 3, 1),
        last_accrued_on=date(2026, 3, 1),
    )
    life.state.loans.append(loan)
    return loan


# ---------------------------------------------------------------------------
# 周结算：每 7 天扣一次房租 + 伙食
# ---------------------------------------------------------------------------


async def test_first_week_is_free_then_rent_and_food_are_deducted() -> None:
    clock, _engine, life = make_life(cash_jpy="100000")

    assert await life.on_timer() == []  # 开局当天不扣钱
    clock.advance(timedelta(days=6))
    await life.on_timer()
    assert life.state.cash_jpy == Decimal("100000")  # 还没到第 7 天

    clock.advance(timedelta(days=1))  # 第 7 天
    events = await life.on_timer()

    assert life.state.cash_jpy == Decimal("50000")  # 扣 35000 房租 + 15000 伙食
    assert life.state.day == 8
    assert len(life.settlements) == 1
    assert any(isinstance(event, LifeEvent) for event in events)


async def test_settlement_repeats_every_seven_days() -> None:
    clock, _engine, life = make_life(cash_jpy="100000")

    clock.advance(timedelta(days=14))
    await life.on_timer()

    assert life.state.cash_jpy == Decimal("0")  # 两周把 10 万花光
    assert life.state.day == 15
    assert len(life.settlements) == 2


async def test_settlement_is_idempotent_within_a_day() -> None:
    clock, _engine, life = make_life(cash_jpy="100000")

    clock.advance(timedelta(days=7))
    await life.on_timer()
    await life.on_timer()
    await life.on_timer()

    assert life.state.cash_jpy == Decimal("50000")
    assert len(life.settlements) == 1


# ---------------------------------------------------------------------------
# 生活费不足：提现 / 强平 / 天台
# ---------------------------------------------------------------------------


async def test_shortfall_is_covered_by_withdrawing_from_trading_account() -> None:
    clock, _engine, life = make_life(cash_jpy="1000", initial_cash={"USD": "1000"})

    clock.advance(timedelta(days=7))
    await life.on_timer()

    assert life.state.status is LifeStatus.ALIVE
    assert life.state.cash_jpy >= Decimal("0")
    assert life.state.forced_liquidations == 0
    assert life.account.available("USD") < Decimal("1000")


async def test_shortfall_forces_liquidation_when_cash_is_locked_in_positions() -> None:
    clock, engine, life = make_life(cash_jpy="1000", initial_cash={"USD": "1000"})
    await feed(engine, "EURUSD", mid=Decimal("1.08500"))
    await engine.place_order(make_order("EURUSD", Side.BUY, "0.18"))
    assert engine.account.positions["EURUSD"].qty == Decimal("0.18")

    clock.advance(timedelta(days=7))
    events = await life.on_timer()

    assert life.state.status is LifeStatus.ALIVE
    assert life.state.forced_liquidations == 1
    assert life.state.cash_jpy >= Decimal("0")
    assert life.state.sanity < Decimal("100")  # 强平扣心态
    assert not any(isinstance(event, GameOverEvent) for event in events)


async def test_unpayable_rent_ends_the_game_on_the_rooftop() -> None:
    clock, _engine, life = make_life(cash_jpy="100", initial_cash={"USD": "0"})

    clock.advance(timedelta(days=7))
    events = await life.on_timer()

    assert life.state.status is LifeStatus.GAME_OVER
    assert life.state.ending is EndingKind.ROOFTOP
    assert any(isinstance(event, GameOverEvent) for event in events)
    # 结束后不再继续扣费
    clock.advance(timedelta(days=7))
    assert await life.on_timer() == []


# ---------------------------------------------------------------------------
# 贷款利息（利滚利）
# ---------------------------------------------------------------------------


async def test_loan_interest_compounds_daily() -> None:
    clock, _engine, life = make_life(cash_jpy="100000")
    loan = add_loan(life)

    clock.advance(timedelta(days=1))
    await life.on_timer()
    assert loan.balance_jpy == Decimal("105000.00")

    clock.advance(timedelta(days=1))
    await life.on_timer()
    assert loan.balance_jpy == Decimal("110250.00")
    assert loan.accrued_interest_jpy == Decimal("10250.00")
    assert loan.status is LoanStatus.ACTIVE


# ---------------------------------------------------------------------------
# 汇率穿透：欠款按真实 USDJPY 折算
# ---------------------------------------------------------------------------


async def test_debt_is_repriced_by_usdjpy() -> None:
    _clock, _engine, life = make_life(cash_jpy="100000")
    add_loan(life, "100000")

    life.usdjpy_provider = lambda: Decimal("150")
    first = life.snapshot()
    assert first.debt_jpy == Decimal("100000.00")
    assert first.debt_usd == Decimal("666.67")
    assert first.usdjpy == Decimal("150.00")

    # 日元贬值（USDJPY 上升）→ 同样的日元欠款折算成更少的美元
    life.usdjpy_provider = lambda: Decimal("200")
    second = life.snapshot()
    assert second.debt_usd == Decimal("500.00")
    assert second.net_equity_usd == second.account_equity_usd - Decimal("500.00")


# ---------------------------------------------------------------------------
# 心态拦截
# ---------------------------------------------------------------------------


async def test_low_sanity_blocks_opening_but_allows_closing() -> None:
    _clock, engine, life = make_life(cash_jpy="100000", initial_cash={"USD": "10000"})
    life.state.sanity = Decimal("10")

    assert not life.can_open_position(make_order("EURUSD", Side.BUY, "0.10"))
    assert not life.can_open_position(make_order("EURUSD", Side.SELL, "0.10"))  # 空仓开空 = 开仓

    await feed(engine, "EURUSD", mid=Decimal("1.08500"))
    await engine.place_order(make_order("EURUSD", Side.BUY, "0.10"))

    # 已有多头，卖出是平仓，必须放行
    assert life.can_open_position(make_order("EURUSD", Side.SELL, "0.10"))

    life.state.sanity = Decimal("100")
    assert life.can_open_position(make_order("EURUSD", Side.SELL, "0.10"))


async def test_large_loss_drains_sanity() -> None:
    _clock, _engine, life = make_life(cash_jpy="100000", initial_cash={"USD": "1000"})
    before = life.state.sanity

    await feed(_engine, "EURUSD", mid=Decimal("1.08500"))
    await _engine.place_order(make_order("EURUSD", Side.BUY, "0.10"))
    await feed(_engine, "EURUSD", mid=Decimal("1.08000"))
    await _engine.place_order(make_order("EURUSD", Side.SELL, "0.10"))

    trade = next(t for t in _engine.list_trades(10) if t.realized_pnl not in (None, Decimal("0")))
    await life.on_event(TradeEvent(trade.ts, trade))

    assert life.state.sanity < before


# ---------------------------------------------------------------------------
# 游戏时钟与离线暂停
# ---------------------------------------------------------------------------


async def test_time_scale_minute_to_hour() -> None:
    base = SimClock(START)
    game = GameClock(base, 60)  # 现实 1 分钟 = 游戏 1 小时

    base.advance(timedelta(seconds=60))
    assert game.now() - START == timedelta(hours=1)

    base.advance(timedelta(seconds=60))
    assert game.now() - START == timedelta(hours=2)


async def test_offline_pause_freezes_game_time() -> None:
    base = SimClock(START)
    game = GameClock(base, 60)

    game.pause()
    base.advance(timedelta(hours=3))
    assert game.now() == START  # 断线期间游戏时间冻结

    offline = game.resume()
    assert offline == timedelta(hours=3)
    base.advance(timedelta(minutes=1))
    assert game.now() - START == timedelta(hours=1)  # 从暂停处继续


async def test_disconnect_pauses_settlement() -> None:
    clock, _engine, life = make_life(cash_jpy="100000")

    life.on_client_connected()
    clock.advance(timedelta(days=7))
    await life.on_timer()
    assert life.state.cash_jpy == Decimal("50000")

    life.on_client_disconnected()  # 离线：时钟冻结 + 调度器挂起
    clock.advance(timedelta(days=7))
    await life.on_timer()  # 即使被调用也不会结算
    assert life.state.cash_jpy == Decimal("50000")
    assert life.state.day == 8

    life.on_client_connected()  # 重连：从断开处继续
    clock.advance(timedelta(days=7))
    await life.on_timer()
    assert life.state.cash_jpy == Decimal("0")


async def test_crossed_weekend_detection() -> None:
    friday = datetime(2026, 3, 6, 15, 0, tzinfo=timezone.utc)
    monday = datetime(2026, 3, 9, 1, 0, tzinfo=timezone.utc)
    assert crossed_weekend(friday, monday) is True

    tuesday = datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)
    wednesday = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)
    assert crossed_weekend(tuesday, wednesday) is False
