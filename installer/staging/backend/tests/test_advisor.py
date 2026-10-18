"""萌智子事件引擎：定时推送 / 立即触发 / 结局后静默。"""

from __future__ import annotations

from datetime import timedelta

from app.game.advisor import AdvisorEngine
from app.game.models import EndingKind
from tests.test_life import make_life


async def test_first_tick_fires_advisor_then_every_three_days() -> None:
    clock, _engine, life = make_life()
    advisor = AdvisorEngine(life, life.settings)

    first = await advisor.on_timer()
    assert len(first) == 1
    event = first[0]
    assert event.kind == "ADVISOR_TIP"
    assert event.title == "萌智子"
    assert "全仓买入" in event.body

    # 同一天不重复推送
    assert await advisor.on_timer() == []

    clock.advance(timedelta(days=2))
    assert await advisor.on_timer() == []

    clock.advance(timedelta(days=1))
    assert len(await advisor.on_timer()) == 1


async def test_trigger_pushes_immediately() -> None:
    _clock, _engine, life = make_life()
    advisor = AdvisorEngine(life, life.settings)

    events = await advisor.trigger()
    assert len(events) == 1
    assert events[0].event_id.startswith("advisor_")


async def test_advisor_is_silent_after_game_over() -> None:
    _clock, _engine, life = make_life()
    advisor = AdvisorEngine(life, life.settings)
    life._end_game(EndingKind.ROOFTOP, "测试结束")

    assert await advisor.on_timer() == []
    assert await advisor.trigger() == []
