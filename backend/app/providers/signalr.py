"""最小可用的 SignalR (ASP.NET Core) JSON 协议客户端。

biquote 的实时推送走 SignalR hub，Python 侧没有官方客户端，这里按协议实现：

    POST /negotiate?negotiateVersion=1     → connectionToken
    WS   wss://<hub>?id=<token>
    →    {"protocol":"json","version":1}\\x1e     (handshake)
    ←    {}\\x1e
    →    {"type":1,"invocationId":"1","target":"Subscribe","arguments":[["EURUSD"]]}\\x1e
    ←    {"type":1,"target":"ReceiveTick","arguments":[{...}]}\\x1e
    ↔    {"type":6}\\x1e                            (心跳，双向)

所有记录以 0x1E 分隔，且可能一帧里粘了多条、也可能半条——分帧逻辑因此单独抽成纯函数，
便于用真实抓包数据做无网络测试。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import websockets

RECORD_SEPARATOR = "\x1e"
log = logging.getLogger("trading.signalr")


class SignalRError(RuntimeError):
    pass


def split_frames(buffer: str) -> tuple[list[str], str]:
    """从缓冲区切出完整记录，返回 (records, 剩余半帧)。"""
    if RECORD_SEPARATOR not in buffer:
        return [], buffer
    parts = buffer.split(RECORD_SEPARATOR)
    rest = parts.pop()  # 最后一段没有终止符，可能是半帧
    return [part for part in parts if part], rest


def decode_frame(frame: str) -> dict[str, Any] | None:
    frame = frame.strip()
    if not frame:
        return None
    try:
        data = json.loads(frame)
    except json.JSONDecodeError:
        log.debug("无法解析的 SignalR 记录: %r", frame[:200])
        return None
    return data if isinstance(data, dict) else None


def invoke_frame(target: str, arguments: Sequence[Any], invocation_id: str) -> str:
    payload = {"type": 1, "invocationId": invocation_id, "target": target, "arguments": list(arguments)}
    return json.dumps(payload, separators=(",", ":")) + RECORD_SEPARATOR


HANDSHAKE = json.dumps({"protocol": "json", "version": 1}) + RECORD_SEPARATOR
PING = json.dumps({"type": 6}) + RECORD_SEPARATOR


class SignalRHub:
    def __init__(
        self,
        hub_url: str,
        *,
        name: str = "hub",
        open_timeout: float = 20.0,
        handshake_timeout: float = 15.0,
        heartbeat: float = 15.0,
    ) -> None:
        self.hub_url = hub_url.rstrip("/")
        self.name = name
        self._open_timeout = open_timeout
        self._handshake_timeout = handshake_timeout
        self._heartbeat = heartbeat
        self._ws: Any = None
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        self._tasks: list[asyncio.Task] = []
        self._invocation_id = 0
        self._closed = False
        self._error: Exception | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._closed

    @property
    def last_error(self) -> Exception | None:
        return self._error

    async def start(self) -> None:
        self._closed = False
        self._error = None

        async with httpx.AsyncClient(timeout=self._open_timeout) as client:
            resp = await client.post(f"{self.hub_url}/negotiate?negotiateVersion=1")
            resp.raise_for_status()
            info = resp.json()

        token = info.get("connectionToken") or info.get("connectionId")
        if not token:
            raise SignalRError(f"{self.name}: negotiate 未返回 connectionToken")

        self._ws = await websockets.connect(
            f"{self.hub_url}?id={token}",
            open_timeout=self._open_timeout,
            ping_interval=None,  # 用 SignalR 自己的 type 6 心跳
            max_size=8 * 1024 * 1024,
        )
        await self._ws.send(HANDSHAKE)
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self._handshake_timeout)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            await self.stop()
            raise SignalRError(f"{self.name}: 握手超时") from exc

        text = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
        firsts, _rest = split_frames(text)
        first = firsts[0] if firsts else text.rstrip(RECORD_SEPARATOR)
        if first.strip() not in ("{}", "") and '"error"' in first:
            await self.stop()
            raise SignalRError(f"{self.name}: 握手失败 {first[:200]}")

        self._tasks.append(asyncio.create_task(self._read_loop(), name=f"{self.name}-read"))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop(), name=f"{self.name}-ping"))

    async def invoke(self, target: str, *arguments: Any) -> str:
        if self._ws is None:
            raise SignalRError(f"{self.name}: 未连接")
        self._invocation_id += 1
        invocation_id = str(self._invocation_id)
        await self._ws.send(invoke_frame(target, arguments, invocation_id))
        return invocation_id

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """按到达顺序产出服务端 invocation 帧（type=1）。"""
        while True:
            message = await self._inbox.get()
            if message.get("__closed__"):
                if self._error is not None:
                    raise self._error
                return
            yield message

    async def stop(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - 关闭阶段的异常无需上抛
                pass
        if not self._inbox.empty():
            return
        self._inbox.put_nowait({"__closed__": True})

    # ---- 内部 ---------------------------------------------------------
    async def _read_loop(self) -> None:
        buffer = ""
        try:
            assert self._ws is not None
            async for raw in self._ws:
                text = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
                buffer += text
                frames, buffer = split_frames(buffer)
                for frame in frames:
                    data = decode_frame(frame)
                    if data is not None:
                        self._dispatch(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 连接中断要能被消费方看到
            self._error = exc
            log.warning("%s 读循环中断: %s", self.name, exc)
        finally:
            self._closed = True
            self._inbox.put_nowait({"__closed__": True})

    def _dispatch(self, data: dict[str, Any]) -> None:
        frame_type = data.get("type")
        if frame_type == 1:  # invocation
            try:
                self._inbox.put_nowait(data)
            except asyncio.QueueFull:
                log.warning("%s 收件箱已满，丢弃一帧", self.name)
        elif frame_type == 7:  # close
            self._error = SignalRError(f"{self.name}: 服务端关闭连接")
            self._closed = True

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat)
            ws = self._ws
            if ws is None:
                return
            try:
                await ws.send(PING)
            except Exception:  # noqa: BLE001
                return
