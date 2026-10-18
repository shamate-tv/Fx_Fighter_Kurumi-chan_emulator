"""外汇真实行情：biquote（零 API key，SignalR 推流 + REST 兜底）。

两条硬约束来自实测与用户提醒：
1. 外汇流里 `last` 和 `volume` 恒为 0 —— 价格**只能**取 `mid`（缺失时退到 bid/ask 中值）。
2. 闭市时服务端会继续推送**同一条旧 tick**（周末尤其明显），因此必须按时间戳年龄判 stale；
   stale 的 tick 只用于画图，MarketHub 会拒绝把它喂给撮合引擎。

降级链：SignalR → REST 轮询 →（都不行时）转交给 fallback（通常是 MockProvider）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from ..core.enums import Market
from ..core.models import Bar, Quote
from ..core.symbols import SYMBOLS, SymbolSpec, get_spec
from .base import MarketDataProvider
from .signalr import SignalRHub

log = logging.getLogger("trading.providers.biquote")
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# 纯解析函数（无网络，便于测试）
# ---------------------------------------------------------------------------

def parse_iso_ts(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _dec(value) -> Decimal:
    if value is None:
        return ZERO
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - 脏数据当 0 处理
        return ZERO


def _is_stale(ts: datetime, now: datetime, stale_after: float) -> bool:
    return (now - ts).total_seconds() > stale_after


def quote_from_signalr(
    payload: dict,
    spec: SymbolSpec,
    now: datetime,
    stale_after: float = 180.0,
) -> Quote | None:
    """解析 ReceiveTick 帧。**忽略 last/volume**，只用 mid。"""
    mid = _dec(payload.get("mid"))
    bid = _dec(payload.get("bid"))
    ask = _dec(payload.get("ask"))

    if mid <= ZERO:
        if bid > ZERO and ask > ZERO:
            mid = (bid + ask) / 2
        else:
            return None

    if bid <= ZERO:
        bid = mid - spec.spread / 2
    if ask <= ZERO:
        ask = mid + spec.spread / 2
    if ask < bid:
        bid, ask = ask, bid

    ts = parse_iso_ts(payload.get("timestamp"))
    return Quote(
        symbol=spec.symbol,
        market=Market.FX,
        bid=spec.normalize_price(bid),
        ask=spec.normalize_price(ask),
        mid=spec.normalize_price(mid),
        ts=ts,
        stale=_is_stale(ts, now, stale_after),
        source="biquote",
    )


def quote_from_rest(
    payload: dict,
    spec: SymbolSpec,
    now: datetime,
    stale_after: float = 180.0,
) -> Quote | None:
    """解析 REST /api/{symbol}。额外尊重服务端给的 stale / marketState。"""
    quote = quote_from_signalr(payload, spec, now, stale_after)
    if quote is None:
        return None
    server_stale = bool(payload.get("stale"))
    market_state = str(payload.get("marketState") or "").lower()
    closed = market_state in {"closed", "break", "pre", "post", "premarket", "postmarket"}
    return quote.model_copy(update={"stale": quote.stale or server_stale or closed})


def bars_from_ohlc(payload: dict, symbol: str, interval: str) -> list[Bar]:
    """biquote 的 OHLC 是"新的在前"，这里统一转成"旧的在前"。"""
    raw_bars = payload.get("bars") or []
    out: list[Bar] = []
    for item in reversed(raw_bars):
        out.append(
            Bar(
                symbol=symbol,
                interval=interval,
                open_time=parse_iso_ts(item.get("openTime")),
                open=_dec(item.get("open")),
                high=_dec(item.get("high")),
                low=_dec(item.get("low")),
                close=_dec(item.get("close")),
                volume=_dec(item.get("tickVolume")),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class BiquoteProvider(MarketDataProvider):
    name = "biquote"
    BASE_URL = "https://biquote.io"
    HUB_URL = "wss://biquote.io/hubs/tick"

    def __init__(
        self,
        *,
        fallback: MarketDataProvider | None = None,
        stale_after: float = 180.0,
        poll_interval: float = 2.0,
        hub_retries: int = 3,
        enabled: bool = True,
    ) -> None:
        self._fallback = fallback
        self._stale_after = stale_after
        self._poll_interval = poll_interval
        self._hub_retries = hub_retries
        self._enabled = enabled
        self._mode = "idle"
        self._last_error: str | None = None

    def supports(self, market: Market) -> bool:
        return market is Market.FX

    def status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self._enabled,
            "mode": self._mode,
            "last_error": self._last_error,
        }

    # ---- 快照 / 历史 ---------------------------------------------------
    async def get_quote(self, symbol: str) -> Quote:
        spec = get_spec(symbol)
        try:
            payload = await self._get_json(f"/api/{spec.symbol}")
            quote = quote_from_rest(payload, spec, datetime.now(timezone.utc), self._stale_after)
            if quote is None:
                raise ValueError("biquote 返回的报价无有效价格")
            return quote
        except Exception as exc:  # noqa: BLE001 - 降级而不是让接口 500
            return await self._degrade_quote(symbol, exc)

    async def get_history(self, symbol: str, interval: str = "1m", limit: int = 200) -> list[Bar]:
        spec = get_spec(symbol)
        try:
            payload = await self._get_json(
                f"/api/{spec.symbol}/ohlc", params={"interval": interval, "limit": limit}
            )
            bars = bars_from_ohlc(payload, spec.symbol, interval)
            if not bars:
                raise ValueError("biquote 未返回 K 线")
            return bars
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            if self._fallback is not None:
                return await self._fallback.get_history(symbol, interval, limit)
            raise

    # ---- 推流 ---------------------------------------------------------
    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        wanted = [s for s in symbols if s in SYMBOLS and SYMBOLS[s].market is Market.FX]
        return self._stream(wanted)

    async def _stream(self, symbols: list[str]) -> AsyncIterator[Quote]:
        if not symbols:
            return
        queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=2000)
        task = asyncio.create_task(self._pump(symbols, queue), name="biquote-pump")
        try:
            while True:
                yield await queue.get()
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾
                pass

    async def _pump(self, symbols: list[str], queue: asyncio.Queue[Quote]) -> None:
        if not self._enabled:
            await self._run_fallback(symbols, queue)
            return

        self._mode = "connecting"
        failures = 0
        while True:
            try:
                await self._run_hub(symbols, queue)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("biquote hub 第 %s 次失败: %s", failures, exc)
                if failures >= self._hub_retries:
                    log.warning("biquote hub 连续失败，改用 REST 轮询")
                    await self._run_rest(symbols, queue)
                    return
                await asyncio.sleep(min(20.0, 2.0**failures))

    async def _run_hub(self, symbols: list[str], queue: asyncio.Queue[Quote]) -> None:
        hub = SignalRHub(self.HUB_URL, name="biquote")
        await hub.start()
        self._mode = "signalr"
        log.info("biquote SignalR 已连接，订阅 %s", symbols)
        try:
            await hub.invoke("Subscribe", symbols)
            async for frame in hub.messages():
                if frame.get("target") != "ReceiveTick":
                    continue
                now = datetime.now(timezone.utc)
                for payload in frame.get("arguments") or []:
                    if not isinstance(payload, dict):
                        continue
                    spec = SYMBOLS.get(str(payload.get("symbol")))
                    if spec is None or spec.market is not Market.FX:
                        continue
                    quote = quote_from_signalr(payload, spec, now, self._stale_after)
                    if quote is not None:
                        self._offer(queue, quote)
        finally:
            await hub.stop()
            if self._mode == "signalr":
                self._mode = "closed"

    async def _run_rest(self, symbols: list[str], queue: asyncio.Queue[Quote]) -> None:
        self._mode = "rest"
        async with httpx.AsyncClient(timeout=15.0) as client:
            while True:
                now = datetime.now(timezone.utc)
                for symbol in symbols:
                    try:
                        resp = await client.get(f"{self.BASE_URL}/api/{symbol}")
                        resp.raise_for_status()
                        quote = quote_from_rest(resp.json(), get_spec(symbol), now, self._stale_after)
                        if quote is not None:
                            self._offer(queue, quote)
                    except Exception:  # noqa: BLE001 - 单个标的失败不影响其它
                        continue
                await asyncio.sleep(self._poll_interval)

    async def _run_fallback(self, symbols: list[str], queue: asyncio.Queue[Quote]) -> None:
        self._mode = "fallback"
        if self._fallback is None:
            log.error("biquote 被禁用且没有 fallback，外汇流为空")
            return
        async for quote in self._fallback.subscribe(symbols):
            self._offer(queue, quote)

    async def close(self) -> None:
        return None

    # ---- 内部 ---------------------------------------------------------
    @staticmethod
    def _offer(queue: asyncio.Queue[Quote], quote: Quote) -> None:
        try:
            queue.put_nowait(quote)
        except asyncio.QueueFull:
            pass

    async def _degrade_quote(self, symbol: str, exc: Exception) -> Quote:
        self._last_error = f"{type(exc).__name__}: {exc}"
        if self._fallback is not None:
            return await self._fallback.get_quote(symbol)
        raise exc

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE_URL}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
