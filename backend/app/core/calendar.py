"""交易日历。

原型精度：只跳周末（已与用户确认）。节假日表后续可无损插入到 HOLIDAYS。
"""

from __future__ import annotations

from datetime import date, timedelta

# 预留：{市场: {日期集合}}。留空表示"只看周末"。
HOLIDAYS: dict[str, set[date]] = {}


def is_business_day(day: date, market: str | None = None) -> bool:
    if day.weekday() >= 5:  # 5=周六 6=周日
        return False
    if market:
        return day not in HOLIDAYS.get(market, set())
    return True


def next_business_day(day: date, market: str | None = None) -> date:
    cursor = day + timedelta(days=1)
    while not is_business_day(cursor, market):
        cursor += timedelta(days=1)
    return cursor


def add_business_days(day: date, n: int, market: str | None = None) -> date:
    """成交日之后第 n 个交易日。n=0 返回当日（不足一个交易日就当天）。"""
    cursor = day
    for _ in range(n):
        cursor = next_business_day(cursor, market)
    return cursor
