"""萌智子事件：定期推销"全仓梭哈"。

只推送叙事消息，绝不修改行情/K 线——买不买、亏不亏都由真实价格决定。
首次事件在"新的一局"开始后的第一个 tick 触发，之后每 N 个游戏日一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..engine.events import DomainEvent
from .clock import game_date

TITLE = "萌智子"
BODY = "我最近研究了一下，这波行情绝对暴涨，赶紧全仓买入！"


@dataclass
class AdvisorEvent(DomainEvent):
    event_id: str
    kind: str
    title: str
    body: str


class AdvisorEngine:
    def __init__(self, life, settings) -> None:
        self._life = life
        self._settings = settings
        self._next_on: date | None = None
        self._seq = 0
        self._pending: list[DomainEvent] = []

    def reset(self) -> None:
        self._next_on = None
        self._seq = 0
        self._pending.clear()

    def mark_undelivered(self, event: DomainEvent) -> None:
        if isinstance(event, AdvisorEvent):
            self._pending.append(event)

    async def on_timer(self, now: datetime | None = None) -> list[DomainEvent]:
        if not self._life.state.alive:
            self._pending.clear()
            return []
        moment = now or self._life.clock.now()
        events: list[DomainEvent] = []
        if self._pending:
            events.extend(self._pending)
            self._pending.clear()
        today = game_date(moment)
        if self._next_on is None:
            # 新的一局：下一次 tick 就来推销，方便玩家立刻体验
            self._next_on = today
        if today < self._next_on:
            return events
        self._next_on = today + timedelta(days=self._settings.advisor_interval_days)
        events.append(self._make_event(moment))
        return events

    async def trigger(self, now: datetime | None = None) -> list[DomainEvent]:
        """调试/演示用：立即推送一条萌智子的建议。"""
        if not self._life.state.alive:
            return []
        moment = now or self._life.clock.now()
        self._next_on = game_date(moment) + timedelta(days=self._settings.advisor_interval_days)
        return [self._make_event(moment)]

    async def ensure_first(self, now: datetime | None = None) -> list[DomainEvent]:
        """新的一局：玩家第一次连上时补一条建议，确保"重开→梭哈"立刻可体验。"""
        if self._seq > 0 or not self._life.state.alive:
            return []
        return await self.trigger(now)

    def _make_event(self, moment: datetime) -> AdvisorEvent:
        self._seq += 1
        return AdvisorEvent(
            moment,
            f"advisor_{self._seq}",
            "ADVISOR_TIP",
            TITLE,
            BODY,
        )
