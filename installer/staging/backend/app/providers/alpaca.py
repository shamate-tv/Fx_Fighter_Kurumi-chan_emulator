"""美股真实行情：Alpaca IEX 行情流（只读行情，绝不触碰交易流）。

- 实时流：`wss://stream.data.alpaca.markets/v2/iex`（由 alpaca-py 的 StockDataStream 在
  `feed=iex` 时使用）。**只订阅 quotes，不下任何交易指令。**
- 快照/历史：走 alpaca-py 的 `StockHistoricalDataClient`（REST）。
- Key 只从环境变量/.env 读取，绝不落进代码或前端。

线程模型：alpaca-py 的 `StockDataStream.run()` 内部用 `asyncio.run()`，因此必须跑在独立
线程里；它的事件回调通过 `loop.call_soon_threadsafe` 把报价送回主事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from decimal import Decimal

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from ..core.enums import Market
from ..core.models import Bar, Quote
from ..core.symbols import SymbolSpec, get_spec
from .base import MarketDataProvider

log = logging.getLogger("trading.providers.alpaca")
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# 纯解析函数（无网络，便于测试）
# ---------------------------------------------------------------------------

def parse_alpaca_ts(raw) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)  # 纳秒 → 微秒
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
    except Exception:  # noqa: BLE001
        return ZERO


def quote_from_alpaca_payload(
    payload: dict,
    spec: SymbolSpec,
    now: datetime | None = None,
    stale_after: float = 300.0,
) -> Quote | None:
    """解析原始流 JSON：{"T":"q","S":"AAPL","bp":179.98,"ap":180.02,"t":"..."}"""
    bid = _dec(payload.get("bp"))
    ask = _dec(payload.get("ap"))
    if bid <= ZERO or ask <= ZERO or ask < bid:
        return None
    return _build(spec, bid, ask, parse_alpaca_ts(payload.get("t")), now, stale_after)


def quote_from_alpaca_quote(
    raw,
    spec: SymbolSpec,
    now: datetime | None = None,
    stale_after: float = 300.0,
) -> Quote | None:
    """解析 alpaca-py 的 Quote 模型对象。"""
    bid = _dec(getattr(raw, "bid_price", None))
    ask = _dec(getattr(raw, "ask_price", None))
    if bid <= ZERO or ask <= ZERO or ask < bid:
        return None
    return _build(spec, bid, ask, parse_alpaca_ts(getattr(raw, "timestamp", None)), now, stale_after)


def _build(
    spec: SymbolSpec,
    bid: Decimal,
    ask: Decimal,
    ts: datetime,
    now: datetime | None,
    stale_after: float,
) -> Quote:
    moment = now or datetime.now(timezone.utc)
    mid = (bid + ask) / 2
    return Quote(
        symbol=spec.symbol,
        market=Market.US,
        bid=spec.normalize_price(bid),
        ask=spec.normalize_price(ask),
        mid=spec.normalize_price(mid),
        ts=ts,
        stale=(moment - ts).total_seconds() > stale_after,
        source="alpaca",
    )


def bars_from_alpaca(raw_bars, symbol: str, interval: str) -> list[Bar]:
    out: list[Bar] = []
    for item in raw_bars or []:
        out.append(
            Bar(
                symbol=symbol,
                interval=interval,
                open_time=parse_alpaca_ts(getattr(item, "timestamp", None)),
                open=_dec(getattr(item, "open", None)),
                high=_dec(getattr(item, "high", None)),
                low=_dec(getattr(item, "low", None)),
                close=_dec(getattr(item, "close", None)),
                volume=_dec(getattr(item, "volume", None)),
            )
        )
    return sorted(out, key=lambda b: b.open_time)


def _timeframe(interval: str) -> TimeFrame:
    mapping = {
        "1m": TimeFrame(1, TimeFrameUnit.Minute),
        "5m": TimeFrame(5, TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "1h": TimeFrame(1, TimeFrameUnit.Hour),
        "1d": TimeFrame(1, TimeFrameUnit.Day),
    }
    return mapping.get(interval, TimeFrame(1, TimeFrameUnit.Minute))


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class AlpacaProvider(MarketDataProvider):
    name = "alpaca"
    STREAM_URL = "wss://stream.data.alpaca.markets/v2/iex"

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        feed: str = "iex",
        fallback: MarketDataProvider | None = None,
        stale_after: float = 300.0,
    ) -> None:
        self._key = (api_key or "").strip()
        self._secret = (secret_key or "").strip()
        self._feed_name = (feed or "iex").lower()
        self._feed = DataFeed.SIP if self._feed_name == "sip" else DataFeed.IEX
        self._fallback = fallback
        self._stale_after = stale_after
        self._mode = "idle"
        self._last_error: str | None = None
        self._client: StockHistoricalDataClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._key and self._secret)

    def supports(self, market: Market) -> bool:
        return market is Market.US

    def status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.configured,
            "feed": self._feed_name,
            "mode": self._mode,
            "last_error": self._last_error,
        }

    # ---- REST ---------------------------------------------------------
    @property
    def client(self) -> StockHistoricalDataClient:
        if self._client is None:
            self._client = StockHistoricalDataClient(self._key, self._secret)
        return self._client

    async def get_quote(self, symbol: str) -> Quote:
        spec = get_spec(symbol)
        if not self.configured:
            return await self._degrade_quote(symbol, RuntimeError("ALPACA_API_KEY/SECRET 未配置"))
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=spec.symbol, feed=self._feed)
            data = await asyncio.to_thread(self.client.get_stock_latest_quote, request)
            raw = data.get(spec.symbol)
            quote = quote_from_alpaca_quote(raw, spec, None, self._stale_after) if raw else None
            if quote is None:
                raise ValueError("Alpaca 返回的报价无有效买卖价")
            return quote
        except Exception as exc:  # noqa: BLE001
            return await self._degrade_quote(symbol, exc)

    async def get_history(self, symbol: str, interval: str = "1m", limit: int = 200) -> list[Bar]:
        spec = get_spec(symbol)
        if not self.configured:
            if self._fallback is not None:
                return await self._fallback.get_history(symbol, interval, limit)
            raise RuntimeError("Alpaca 未配置")
        try:
            request = StockBarsRequest(
                symbol_or_symbols=spec.symbol,
                timeframe=_timeframe(interval),
                limit=max(1, min(limit, 1000)),
                feed=self._feed,
            )
            result = await asyncio.to_thread(self.client.get_stock_bars, request)
            rows = result.data.get(spec.symbol, [])
            bars = bars_from_alpaca(rows, spec.symbol, interval)
            if not bars:
                raise ValueError("Alpaca 未返回 K 线")
            return bars
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            if self._fallback is not None:
                return await self._fallback.get_history(symbol, interval, limit)
            raise

    # ---- 推流 ---------------------------------------------------------
    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        return self._stream(list(symbols))

    async def _stream(self, symbols: list[str]) -> AsyncIterator[Quote]:
        if not symbols:
            return
        if not self.configured:
            if self._fallback is None:
                log.error("Alpaca 未配置 key 且没有 fallback，美股流为空")
                return
            self._mode = "fallback"
            async for quote in self._fallback.subscribe(symbols):
                yield quote
            return

        main_loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=2000)
        stream = StockDataStream(self._key, self._secret, feed=self._feed)

        async def on_quote(raw) -> None:  # alpaca-py 要求 async 回调
            try:
                spec = get_spec(raw.symbol)
            except Exception:  # noqa: BLE001 - 未注册的标的直接忽略
                return
            quote = quote_from_alpaca_quote(raw, spec, None, self._stale_after)
            if quote is None:
                return
            try:
                main_loop.call_soon_threadsafe(self._offer, queue, quote)
            except RuntimeError:
                pass

        for symbol in symbols:
            stream.subscribe_quotes(on_quote, symbol)

        thread = threading.Thread(
            target=self._run_stream,
            args=(stream,),
            name="alpaca-stream",
            daemon=True,
        )
        thread.start()
        self._mode = "stream"
        log.info("Alpaca IEX 行情流启动，订阅 %s", symbols)

        try:
            while True:
                yield await queue.get()
        finally:
            await self._shutdown(stream, thread)

    def _run_stream(self, stream: StockDataStream) -> None:
        try:
            stream.run()
        except Exception as exc:  # noqa: BLE001 - 线程内异常只记录
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Alpaca 流线程退出: %s", exc)
        finally:
            if self._mode == "stream":
                self._mode = "closed"

    async def _shutdown(self, stream: StockDataStream, thread: threading.Thread) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(stream.stop), timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.debug("Alpaca stream.stop 异常: %s", exc)
        thread.join(timeout=5)
        if self._mode == "stream":
            self._mode = "closed"

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
