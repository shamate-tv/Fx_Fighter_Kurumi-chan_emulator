"""APScheduler 驱动的生存系统定时任务。

每秒检查一次游戏时间是否跨天/跨周；离线时 pause() 挂起，连上时 resume()。
与行情 Hub 的 5 秒交收 timer 解耦——游戏时间暂停时，交收与行情照常。
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger("trading.game")


class LifeScheduler:
    def __init__(self, life, ws, interval_seconds: float = 1.0, event_engine=None) -> None:
        self._life = life
        self._ws = ws
        self._interval = interval_seconds
        self._event_engine = event_engine
        self._scheduler: AsyncIOScheduler | None = None

    def start(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._scheduler.add_job(
            self._tick,
            "interval",
            seconds=self._interval,
            id="life-tick",
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        # 没有玩家在线时，游戏默认暂停（离线不扣费）
        self._scheduler.pause()
        log.info("生存系统定时任务已启动（离线暂停中，等待玩家连接）")

    async def _tick(self) -> None:
        try:
            events = await self._life.on_timer()
            if self._event_engine is not None:
                events.extend(await self._event_engine.on_timer())
        except Exception:  # noqa: BLE001 - 单次结算异常不能杀掉调度器
            log.exception("生存系统 tick 失败")
            return
        for event in events:
            sent = await self._ws.broadcast_event(event)
            if sent == 0 and self._event_engine is not None:
                # 没有在线玩家时不要把事件"消费"掉，下次 tick 重发
                self._event_engine.mark_undelivered(event)

    def pause(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.pause()

    def resume(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.resume()

    def shutdown(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None
