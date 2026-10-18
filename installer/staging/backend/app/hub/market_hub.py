"""MarketHub：聚合订阅、缓存最近 tick、把行情喂给引擎、再把事件扇出给 WS。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from ..core.clock import Clock
from ..core.models import Quote
from ..core.symbols import SYMBOLS
from ..engine.events import DomainEvent, QuoteEvent
from ..engine.trading_engine import TradingEngine
from ..providers.router import ProviderRouter

EventSink = Callable[[DomainEvent], Awaitable[None]]
TimerSink = Callable[[], Awaitable[list[DomainEvent]]]


class MarketHub:
    def __init__(
        self,
        router: ProviderRouter,
        engine: TradingEngine,
        clock: Clock,
        *,
        settlement_scan_interval: float = 5.0,
    ) -> None:
        self.router = router
        self.engine = engine
        self.clock = clock
        self.settlement_scan_interval = settlement_scan_interval
        self.last_quotes: dict[str, Quote] = {}
        self._sinks: list[EventSink] = []
        self._timer_sinks: list[TimerSink] = []
        self._symbols: list[str] = list(SYMBOLS)
        self._tasks: list[asyncio.Task] = []

    # ---- 生命周期 -----------------------------------------------------
    def add_sink(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def add_timer_sink(self, sink: TimerSink) -> None:
        """挂到行情 Hub 的定时循环上：与撮合/交收共用同一个时间源。"""
        self._timer_sinks.append(sink)

    async def start(self) -> None:
        await self.router.start()
        # 预热：并发抓快照，保证第一个 tick 到达前市价单也能成交。
        # 真实数据源可能慢/不可达，所以每个标的独立超时，互不拖累。
        async def prime(symbol: str) -> None:
            try:
                quote = await asyncio.wait_for(self.router.get_quote(symbol), timeout=10)
            except Exception:  # noqa: BLE001 - 单个标的失败不影响整体启动
                return
            self.last_quotes[symbol] = quote
            # 只有新鲜报价才允许进入撮合用价；闭市/陈旧的快照只拿去画图
            if not quote.stale:
                self.engine.last_quotes[symbol] = quote
            await self._emit(QuoteEvent(quote.ts, quote))

        await asyncio.gather(*(prime(symbol) for symbol in self._symbols))
        self._tasks.append(asyncio.create_task(self._consume(), name="market-hub-consume"))
        self._tasks.append(asyncio.create_task(self._timer_loop(), name="market-hub-timer"))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        await self.router.close()

    # ---- 自定义标的 -----------------------------------------------------
    async def add_symbol(self, symbol: str) -> None:
        """注册后立刻纳入行情：预热快照 → 广播 → 重启消费流以订阅新标的。"""
        if symbol in self._symbols:
            return
        self._symbols.append(symbol)

        try:
            quote = await asyncio.wait_for(self.router.get_quote(symbol), timeout=5)
        except Exception:  # noqa: BLE001 - 预热失败不影响注册，下个 tick 会补
            quote = None
        if quote is not None:
            self.last_quotes[symbol] = quote
            if not quote.stale:
                self.engine.last_quotes[symbol] = quote
            await self._emit(QuoteEvent(quote.ts, quote))

        await self._restart_consume()

    async def _restart_consume(self) -> None:
        for task in list(self._tasks):
            if task.get_name() != "market-hub-consume":
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._tasks.remove(task)
            break
        self._tasks.append(asyncio.create_task(self._consume(), name="market-hub-consume"))

    # ---- 后台任务 -----------------------------------------------------
    async def _consume(self) -> None:
        async for quote in self.router.subscribe(self._symbols):
            self.last_quotes[quote.symbol] = quote
            await self._emit(QuoteEvent(quote.ts, quote))
            try:
                events = await self.engine.on_quote(quote)
            except Exception:  # noqa: BLE001 - 撮合异常不能杀掉行情流
                events = []
            for event in events:
                await self._emit(event)

    async def _timer_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settlement_scan_interval)
            try:
                events = await self.engine.on_timer(self.clock.now())
            except Exception:  # noqa: BLE001
                events = []
            for event in events:
                await self._emit(event)
            # 生存系统/事件引擎与交收共用这个 tick
            for sink in list(self._timer_sinks):
                try:
                    extra = await sink()
                except Exception:  # noqa: BLE001 - 生存系统异常不能拖垮行情
                    extra = []
                for event in extra:
                    await self._emit(event)

    # ---- 扇出 ---------------------------------------------------------
    async def _emit(self, event: DomainEvent) -> None:
        for sink in self._sinks:
            try:
                await sink(event)
            except Exception:  # noqa: BLE001 - 一个连接出错不影响其它
                continue
