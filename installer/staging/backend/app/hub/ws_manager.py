"""WebSocket 连接管理与事件序列化。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from fastapi import WebSocket
from pydantic import BaseModel

from ..engine.events import AccountEvent, DomainEvent, OrderEvent, QuoteEvent, SettlementEvent, TradeEvent
from ..game.events import GameOverEvent, LifeEvent
from ..game.advisor import AdvisorEvent

DEFAULT_CHANNELS = {"tick", "account", "order", "trade", "settlement", "life", "event"}


def jsonable(value: Any) -> Any:
    """把 Decimal / datetime / Enum / BaseModel 递归转成可 json 序列化的结构。"""
    if isinstance(value, Enum):  # 必须早于 str 判断：Market 是 str 的子类
        return value.value
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def serialize_event(event: DomainEvent) -> tuple[str, dict] | None:
    """返回 (channel, payload)。"""
    if isinstance(event, QuoteEvent):
        q = event.quote
        return "tick", {
            "type": "tick",
            "symbol": q.symbol,
            "market": q.market.value,
            "bid": str(q.bid),
            "ask": str(q.ask),
            "mid": str(q.mid),
            "ts": q.ts.isoformat(),
            "stale": q.stale,
            "source": q.source,
        }
    if isinstance(event, TradeEvent):
        return "trade", {"type": "trade", "trade": jsonable(event.trade)}
    if isinstance(event, OrderEvent):
        return "order", {"type": "order", "order": jsonable(event.order)}
    if isinstance(event, AccountEvent):
        return "account", {"type": "account", "account": jsonable(event.account)}
    if isinstance(event, SettlementEvent):
        item = event.item
        return "settlement", {
            "type": "settlement",
            "item": {
                "id": item.id,
                "amount": str(item.amount),
                "currency": item.currency,
                "market": item.market.value,
                "trade_id": item.trade_id,
                "release_at": item.release_at.isoformat(),
            },
        }
    if isinstance(event, LifeEvent):
        return "life", {"type": "life", "life": jsonable(event.life)}
    if isinstance(event, GameOverEvent):
        return "game_over", {
            "type": "game_over",
            "ending": event.ending.value,
            "reason": event.reason,
            "day": event.day,
        }
    if isinstance(event, AdvisorEvent):
        return "event", {
            "type": "event",
            "event": {
                "id": event.event_id,
                "kind": event.kind,
                "title": event.title,
                "body": event.body,
            },
        }
    return None


@dataclass
class ClientConn:
    websocket: WebSocket
    symbols: set[str] = field(default_factory=set)
    channels: set[str] = field(default_factory=lambda: set(DEFAULT_CHANNELS))


class WsManager:
    def __init__(self) -> None:
        self._conns: list[ClientConn] = []
        self._lock = asyncio.Lock()
        self._on_first_connect = None
        self._on_last_disconnect = None

    @property
    def connection_count(self) -> int:
        return len(self._conns)

    def bind_lifecycle(self, on_first_connect=None, on_last_disconnect=None) -> None:
        """把"第一个连接 / 最后一个断开"交给生存系统，驱动离线暂停。

        服务端在推送失败时也会调用 disconnect()，所以暂停/恢复必须由
        WsManager 统一触发，不能只依赖路由的 finally。
        """
        self._on_first_connect = on_first_connect
        self._on_last_disconnect = on_last_disconnect

    async def connect(self, websocket: WebSocket, data_sources: dict[str, str], server_time: datetime) -> ClientConn:
        await websocket.accept()
        first = len(self._conns) == 0
        conn = ClientConn(websocket=websocket)
        self._conns.append(conn)
        if first and self._on_first_connect is not None:
            self._on_first_connect()
        await self._safe_send(
            conn,
            {
                "type": "hello",
                "server_time": server_time.isoformat(),
                "data_sources": data_sources,
                "channels": sorted(DEFAULT_CHANNELS),
            },
        )
        return conn

    def disconnect(self, conn: ClientConn) -> None:
        before = len(self._conns)
        self._conns = [c for c in self._conns if c is not conn]
        if before > 0 and not self._conns and self._on_last_disconnect is not None:
            self._on_last_disconnect()

    async def handle_message(self, conn: ClientConn, payload: dict) -> None:
        kind = payload.get("type")
        if kind == "subscribe":
            symbols = payload.get("symbols") or []
            channels = payload.get("channels") or []
            if symbols:
                conn.symbols.update(str(s) for s in symbols)
            if channels:
                conn.channels.update(str(c) for c in channels)
            await self._safe_send(
                conn,
                {"type": "subscribed", "symbols": sorted(conn.symbols), "channels": sorted(conn.channels)},
            )
        elif kind == "unsubscribe":
            symbols = payload.get("symbols") or []
            for symbol in symbols:
                conn.symbols.discard(str(symbol))
            await self._safe_send(conn, {"type": "unsubscribed", "symbols": sorted(conn.symbols)})
        elif kind == "ping":
            await self._safe_send(conn, {"type": "pong"})

    async def broadcast(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            await self.broadcast_event(event)

    async def broadcast_event(self, event: DomainEvent) -> int:
        """返回成功送达的连接数（0 表示没有在线客户端）。"""
        serialized = serialize_event(event)
        if serialized is None:
            return 0
        channel, payload = serialized
        sent = 0
        for conn in list(self._conns):
            if not self._accepts(conn, channel, payload):
                continue
            if await self._safe_send(conn, payload):
                sent += 1
        return sent

    async def send_snapshot(self, conn: ClientConn, payload: dict) -> None:
        await self._safe_send(conn, payload)

    # ---- 内部 ---------------------------------------------------------
    @staticmethod
    def _accepts(conn: ClientConn, channel: str, payload: dict) -> bool:
        if channel not in conn.channels:
            return False
        if channel == "tick" and conn.symbols:
            return payload.get("symbol") in conn.symbols
        return True

    async def _safe_send(self, conn: ClientConn, payload: dict) -> bool:
        try:
            await conn.websocket.send_json(payload)
            return True
        except Exception:  # noqa: BLE001 - 连接已断
            self.disconnect(conn)
            return False
