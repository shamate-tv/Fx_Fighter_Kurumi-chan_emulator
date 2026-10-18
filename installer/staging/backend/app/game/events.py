"""生存系统领域事件：由 LifeEngine 产出，WsManager 负责序列化广播。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..engine.events import DomainEvent
from .models import EndingKind, LifeOut


@dataclass
class LifeEvent(DomainEvent):
    life: LifeOut


@dataclass
class GameOverEvent(DomainEvent):
    ending: EndingKind
    reason: str
    day: int
