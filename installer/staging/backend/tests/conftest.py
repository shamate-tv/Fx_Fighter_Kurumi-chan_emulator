from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.clock import SimClock
from app.engine.account import Account
from app.engine.trading_engine import TradingEngine

START = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)  # 周一


@pytest.fixture
def clock() -> SimClock:
    return SimClock(START)


@pytest.fixture
def engine(clock: SimClock) -> TradingEngine:
    account = Account({"USD": Decimal("100000"), "HKD": Decimal("800000")})
    return TradingEngine(account, clock)


@pytest.fixture
def tight_engine(clock: SimClock) -> TradingEngine:
    """港股现金刚好只够买一笔：卖出所得必须能撑起第二笔，否则就是"同市场即时再投资"没生效。"""
    account = Account({"USD": Decimal("0"), "HKD": Decimal("50000")})
    return TradingEngine(account, clock)
