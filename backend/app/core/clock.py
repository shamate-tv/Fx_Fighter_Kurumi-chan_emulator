"""时钟抽象：真实时钟 / 可快进的模拟时钟。

交收（T+1 / T+2）必须能在测试里被精确验证，所以任何涉及"现在几点"的地方
都只能读 Clock，不能直接调 datetime.now()。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimClock:
    """手动推进的时钟，供测试与 /api/admin/advance-time 使用。"""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("模拟时钟只能向前推进")
        self._now += delta
        return self._now

    def set(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            raise ValueError("模拟时钟需要带时区的时间")
        self._now = moment
        return self._now
