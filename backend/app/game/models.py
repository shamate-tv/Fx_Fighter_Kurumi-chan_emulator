"""生存系统的数据模型。

运行时用 dataclass（与 Account 一致），对外用 Pydantic 输出。
若后续要落 SQLite，按设计里的 4 张表（game_state / loans / daily_settlements / game_events）
把这里的字段直接映射即可；当前与账户、成交一样是内存态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

ZERO = Decimal("0")


class LifeStatus(str, Enum):
    ALIVE = "ALIVE"
    GAME_OVER = "GAME_OVER"


class EndingKind(str, Enum):
    ROOFTOP = "ROOFTOP"  # 付不起房租/伙食 → 天台结局
    DEBT_COLLAPSE = "DEBT_COLLAPSE"  # 利滚利违约 → 黑市催收
    SANITY_CRASH = "SANITY_CRASH"  # 心态归零 → 精神崩溃
    WIPEOUT = "WIPEOUT"  # 彻底爆仓且无资产


class LoanStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REPAID = "REPAID"
    DEFAULTED = "DEFAULTED"


@dataclass
class Loan:
    id: str
    lender: str
    principal_jpy: Decimal
    balance_jpy: Decimal
    daily_rate: Decimal
    taken_on: date
    last_accrued_on: date
    due_date: date | None = None
    accrued_interest_jpy: Decimal = ZERO
    status: LoanStatus = LoanStatus.ACTIVE


@dataclass
class SettlementRecord:
    """一次周结算的明细（房租 + 伙食 + 当期利息）。"""

    game_date: date
    rent: Decimal
    food: Decimal
    interest: Decimal
    cash_before: Decimal
    cash_after: Decimal
    forced_liquidation: bool = False
    ending: EndingKind | None = None


@dataclass
class LifeState:
    game_date: date
    last_settled_on: date
    cash_jpy: Decimal
    rent_jpy: Decimal
    food_jpy: Decimal
    sanity: Decimal
    day: int = 1
    status: LifeStatus = LifeStatus.ALIVE
    ending: EndingKind | None = None
    ending_reason: str | None = None
    next_settle_on: date | None = None
    consecutive_losses: int = 0
    forced_liquidations: int = 0
    loans: list[Loan] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.status is LifeStatus.ALIVE

    @property
    def debt_jpy(self) -> Decimal:
        return sum((loan.balance_jpy for loan in self.loans if loan.status is LoanStatus.ACTIVE), ZERO)


def caution_level(sanity: Decimal) -> int:
    """心态 → 前端幻觉等级：0 正常 / 1 焦虑 / 2 幻觉 / 3 崩溃边缘。"""
    if sanity < Decimal("15"):
        return 3
    if sanity < Decimal("30"):
        return 2
    if sanity < Decimal("60"):
        return 1
    return 0


# ---------------------------------------------------------------------------
# 对外输出
# ---------------------------------------------------------------------------


class LoanOut(BaseModel):
    id: str
    lender: str
    principal_jpy: Decimal
    balance_jpy: Decimal
    daily_rate: Decimal
    accrued_interest_jpy: Decimal
    taken_on: date
    due_date: date | None
    status: LoanStatus


class LifeOut(BaseModel):
    day: int
    game_date: date
    game_time: datetime  # 游戏内时间（离线暂停时冻结）
    cash_jpy: Decimal
    rent_jpy: Decimal
    food_jpy: Decimal
    weekly_cost_jpy: Decimal
    next_settle_on: date | None
    sanity: Decimal
    caution_level: int
    status: LifeStatus
    ending: EndingKind | None
    ending_reason: str | None
    online: bool  # 是否有玩家在线；false 表示游戏时间已暂停
    debt_jpy: Decimal
    # 汇率穿透：欠款按当前真实 USDJPY 折算，汇率波动会放大负债
    usdjpy: Decimal
    debt_usd: Decimal
    account_equity_usd: Decimal
    net_equity_usd: Decimal
    forced_liquidations: int
    loans: list[LoanOut]


class GameOverOut(BaseModel):
    ending: EndingKind
    reason: str
    day: int
