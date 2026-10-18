"""领域事件：引擎吐事件，WS 层负责序列化广播。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.models import AccountSnapshot, Order, Quote, Trade
from .account import SettlementItem


@dataclass
class DomainEvent:
    ts: datetime


@dataclass
class QuoteEvent(DomainEvent):
    quote: Quote


@dataclass
class OrderEvent(DomainEvent):
    order: Order


@dataclass
class TradeEvent(DomainEvent):
    trade: Trade


@dataclass
class AccountEvent(DomainEvent):
    account: AccountSnapshot


@dataclass
class SettlementEvent(DomainEvent):
    item: SettlementItem
