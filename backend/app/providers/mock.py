"""MockProvider：零网络、零 key，三市场全覆盖。

价格由几何随机游走生成（每 tick 相对波动 = sigma_bps / 10000），
bid/ask 由品种点差推出，历史 K 线用同一 seed 确定性生成，重启后图形连续。

严格遵守用户提醒：`last` / `volume` 在真实外汇流里恒为 0，
所以这里也只暴露 bid/ask/mid，不提供 last/volume 语义。
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..core.enums import ErrorCode, Market, TradingError
from ..core.models import Bar, Quote
from ..core.symbols import SYMBOLS, SymbolSpec
from .base import MarketDataProvider

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


class MockProvider(MarketDataProvider):
    name = "mock"

    def __init__(
        self,
        specs: dict[str, SymbolSpec] | None = None,
        *,
        seed: int = 20260302,
        tick_interval: float = 0.4,
    ) -> None:
        self._specs: dict[str, SymbolSpec] = dict(specs or SYMBOLS)
        self._seed = seed
        self._rng = random.Random(seed)
        self._mid: dict[str, float] = {s.symbol: float(s.start_price) for s in self._specs.values()}
        self._tick_interval = tick_interval
        self._subscribers: list[asyncio.Queue[Quote]] = []
        self._live_symbols: set[str] = set()
        self._task: asyncio.Task | None = None
        self._history_cache: dict[tuple[str, str, int], list[Bar]] = {}

    def supports(self, market: Market) -> bool:
        return True

    def add_spec(self, spec: SymbolSpec) -> None:
        """运行期注册自定义标的：纳入报价循环，并从参考价开始随机游走。"""
        self._specs[spec.symbol] = spec
        self._mid[spec.symbol] = float(spec.start_price)

    # ---- 快照 ---------------------------------------------------------
    async def get_quote(self, symbol: str) -> Quote:
        spec = self._require(symbol)
        return self._make_quote(spec, self._mid[spec.symbol], datetime.now(timezone.utc))

    # ---- 历史 ---------------------------------------------------------
    async def get_history(self, symbol: str, interval: str = "1m", limit: int = 200) -> list[Bar]:
        spec = self._require(symbol)
        if interval not in INTERVAL_SECONDS:
            interval = "1m"
        limit = max(1, min(limit, 2000))
        key = (spec.symbol, interval, limit)
        cached = self._history_cache.get(key)
        if cached is not None:
            return cached

        step = INTERVAL_SECONDS[interval]
        # 每个 bar 的波动 = 每 tick 波动 × sqrt(bar 内的 tick 数)
        per_tick_sigma = float(spec.sigma_bps) / 10_000
        sigma = per_tick_sigma * math.sqrt(max(1.0, step / 1.0))
        rng = random.Random(f"{self._seed}:{spec.symbol}:{interval}")

        prices = [self._mid[spec.symbol]]
        for _ in range(limit):
            prices.append(prices[-1] * math.exp(rng.gauss(0.0, sigma)))
        prices.reverse()  # prices[0] 最早，prices[-1] = 当前 mid

        now = datetime.now(timezone.utc)
        aligned = now.replace(second=0, microsecond=0)
        bars: list[Bar] = []
        for i in range(limit):
            open_time = aligned - timedelta(seconds=step * (limit - 1 - i))
            o = prices[i]
            c = prices[i + 1]
            hi = max(o, c) * (1 + abs(rng.gauss(0, sigma / 3)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, sigma / 3)))
            bars.append(
                Bar(
                    symbol=spec.symbol,
                    interval=interval,
                    open_time=open_time,
                    open=self._dec(o, spec),
                    high=self._dec(hi, spec),
                    low=self._dec(lo, spec),
                    close=self._dec(c, spec),
                    volume=Decimal(str(round(rng.uniform(500, 5000), 2))),
                )
            )
        self._history_cache[key] = bars
        return bars

    # ---- 推流 ---------------------------------------------------------
    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        wanted = [s for s in symbols if s in self._specs]
        queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=1000)
        self._subscribers.append(queue)
        self._live_symbols.update(wanted)
        self._ensure_task()
        return self._iter(queue)

    async def _iter(self, queue: asyncio.Queue[Quote]) -> AsyncIterator[Quote]:
        try:
            while True:
                yield await queue.get()
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="mock-provider-ticker")

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._tick_interval)
                if not self._subscribers:
                    continue
                now = datetime.now(timezone.utc)
                for symbol in list(self._live_symbols):
                    spec = self._specs[symbol]
                    self._mid[symbol] = self._step(spec, self._mid[symbol])
                    quote = self._make_quote(spec, self._mid[symbol], now)
                    for queue in list(self._subscribers):
                        try:
                            queue.put_nowait(quote)
                        except asyncio.QueueFull:
                            pass
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._subscribers.clear()
        self._live_symbols.clear()

    # ---- 内部 ---------------------------------------------------------
    def _step(self, spec: SymbolSpec, mid: float) -> float:
        sigma = float(spec.sigma_bps) / 10_000
        return mid * math.exp(self._rng.gauss(0.0, sigma))

    def _make_quote(self, spec: SymbolSpec, mid: float, ts: datetime) -> Quote:
        half = float(spec.spread) / 2
        bid = mid - half
        ask = mid + half
        return Quote(
            symbol=spec.symbol,
            market=spec.market,
            bid=self._dec(bid, spec),
            ask=self._dec(ask, spec),
            mid=self._dec(mid, spec),
            ts=ts,
            stale=False,
            source="mock",
        )

    @staticmethod
    def _dec(value: float, spec: SymbolSpec) -> Decimal:
        quantum = Decimal(1).scaleb(-spec.price_precision)
        return Decimal(str(value)).quantize(quantum)

    def _require(self, symbol: str) -> SymbolSpec:
        spec = self._specs.get(symbol)
        if spec is None:
            raise TradingError(ErrorCode.UNKNOWN_SYMBOL, f"MockProvider 不认识 {symbol}", {"symbol": symbol})
        return spec
