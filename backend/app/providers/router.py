"""按市场路由到具体 Provider，并把多条推流合并成一条异步迭代器。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence

from ..core.enums import Market
from ..core.models import Bar, Quote
from ..core.symbols import get_spec, is_custom
from .base import MarketDataProvider


class ProviderRouter:
    def __init__(self, providers: dict[Market, MarketDataProvider], fallback: MarketDataProvider) -> None:
        self._fallback = fallback
        self._active: dict[Market, MarketDataProvider] = {}
        for market in Market:
            candidate = providers.get(market)
            self._active[market] = candidate if candidate is not None else fallback

    def provider_for(self, market: Market) -> MarketDataProvider:
        return self._active[market]

    def for_symbol(self, symbol: str) -> MarketDataProvider:
        # 自定义标的没有真实数据源，固定走 Mock 兜底
        if is_custom(symbol):
            return self._fallback
        return self._active[get_spec(symbol).market]

    def data_sources(self) -> dict[str, str]:
        return {m.value: p.name for m, p in self._active.items()}

    def provider_status(self) -> dict[str, dict]:
        """供 /api/health：每个市场的真实连接状态（不含密钥）。"""
        return {m.value: p.status() for m, p in self._active.items()}

    async def get_quote(self, symbol: str) -> Quote:
        return await self.for_symbol(symbol).get_quote(symbol)

    async def get_history(self, symbol: str, interval: str = "1m", limit: int = 200) -> list[Bar]:
        return await self.for_symbol(symbol).get_history(symbol, interval, limit)

    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        return self._merged(symbols)

    async def _merged(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=2000)
        grouped: dict[MarketDataProvider, list[str]] = defaultdict(list)
        for symbol in symbols:
            grouped[self.for_symbol(symbol)].append(symbol)

        async def pump(provider: MarketDataProvider, syms: list[str]) -> None:
            try:
                async for tick in provider.subscribe(syms):
                    await queue.put(tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 单个 provider 掉线不应拖垮整条流
                return

        tasks = [asyncio.create_task(pump(p, s), name=f"feed-{p.name}") for p, s in grouped.items()]
        try:
            while True:
                yield await queue.get()
        finally:
            for task in tasks:
                task.cancel()

    async def start(self) -> None:
        for provider in set(self._active.values()):
            await provider.start()

    async def close(self) -> None:
        for provider in set(self._active.values()):
            await provider.close()
