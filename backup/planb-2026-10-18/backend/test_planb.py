"""Plan B：B站求关注换生活费 + 24 小时冷却。"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.enums import ErrorCode, TradingError
from tests.test_life import make_life


async def test_planb_claim_adds_cash_and_starts_cooldown() -> None:
    clock, _engine, life = make_life(cash_jpy="100000")

    result = await life.claim_planb(uid="123456")

    assert result["ok"] is True
    assert result["gain_jpy"] == "50000"
    assert life.state.cash_jpy == Decimal("150000")
    assert life.state.planb_claims == 1
    assert life.snapshot().planb_ready is False
    assert life.snapshot().planb_cooldown_seconds > 0

    with pytest.raises(TradingError) as error:
        await life.claim_planb(uid="123456")
    assert error.value.code is ErrorCode.PLANB_COOLDOWN
    assert error.value.details["remaining_seconds"] > 0

    # 冷却结束后可以再薅一次
    clock.advance(timedelta(days=1))
    assert life.snapshot().planb_ready is True
    await life.claim_planb(uid="123456")
    assert life.state.cash_jpy == Decimal("200000")
    assert life.state.planb_claims == 2


async def test_planb_rejects_invalid_uid() -> None:
    _clock, _engine, life = make_life(cash_jpy="100000")

    with pytest.raises(TradingError) as error:
        await life.claim_planb(uid="not-a-uid")

    assert error.value.code is ErrorCode.PLANB_INVALID_UID
    assert life.state.cash_jpy == Decimal("100000")
