"""游戏时钟：把基础时钟按 TIME_SCALE 加速成"游戏世界时间"。

- TIME_SCALE=60 ：现实 60 秒 = 游戏 1 小时（1 天 = 现实 24 分钟）
- TIME_SCALE=1  ：与基础时钟 1:1（测试/回放）

离线暂停
--------
`pause()` 冻结游戏时间，`resume()` 把基础时间基准整体前移，于是重连后
游戏时间从断开的那一刻继续——断线期间不扣房租、不计利息。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..core.clock import Clock

JST = timedelta(hours=9)


class GameClock:
    def __init__(self, base: Clock, scale: float = 1.0, start: datetime | None = None) -> None:
        self._base = base
        self._scale = max(1.0, float(scale))
        self._base_epoch = base.now()
        self._game_epoch = start or self._base_epoch
        self._paused_at: datetime | None = None

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    @property
    def paused_at(self) -> datetime | None:
        """暂停发生时的基础时钟时刻（用于判断是否跨周末）。"""
        return self._paused_at

    def now(self) -> datetime:
        if self._paused_at is not None:
            return self._game_epoch + (self._paused_at - self._base_epoch) * self._scale
        return self._game_epoch + (self._base.now() - self._base_epoch) * self._scale

    def pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = self._base.now()

    def resume(self) -> timedelta:
        """恢复游戏时间；返回离线了多久。"""
        if self._paused_at is None:
            return timedelta(0)
        offline = self._base.now() - self._paused_at
        self._base_epoch += offline
        self._paused_at = None
        return offline


def game_date(moment: datetime) -> date:
    """游戏日历用 JST（东京）日期，和日本原作的语境一致。"""
    return (moment + JST).date()


def crossed_weekend(start: datetime, end: datetime) -> bool:
    """离线区间是否覆盖了周六/周日（外汇休市）。"""
    if end <= start:
        return False
    day = start.date()
    last = end.date()
    while day <= last:
        if day.weekday() >= 5:
            return True
        day += timedelta(days=1)
    return False
