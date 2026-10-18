"""领域模型（Pydantic）：API 输出与引擎内部共用的数据结构。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .enums import Market, OrderStatus, OrderType, Side


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    market: Market
    bid: Decimal
    ask: Decimal
    mid: Decimal
    ts: datetime
    stale: bool = False
    source: str = "mock"


class Bar(BaseModel):
    symbol: str
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")


class OrderRequest(BaseModel):
    symbol: str
    side: Side
    type: OrderType
    qty: Decimal = Field(gt=0)
    limit_price: Decimal | None = None
    client_order_id: str | None = None


class FeeComponent(BaseModel):
    name: str
    rate: Decimal
    amount: Decimal
    currency: str


class Order(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: str
    symbol: str
    market: Market
    side: Side
    type: OrderType
    qty: Decimal
    filled_qty: Decimal = Decimal("0")
    limit_price: Decimal | None = None
    status: OrderStatus = OrderStatus.PENDING
    avg_fill_price: Decimal | None = None
    fee: Decimal = Decimal("0")
    created_at: datetime
    updated_at: datetime
    reject_reason: str | None = None
    client_order_id: str | None = None


class Trade(BaseModel):
    id: str
    order_id: str
    symbol: str
    market: Market
    side: Side
    qty: Decimal
    price: Decimal
    gross: Decimal
    fees: list[FeeComponent] = Field(default_factory=list)
    fee_total: Decimal = Decimal("0")
    currency: str
    trade_date: date
    settles_on: date | None = None
    realized_pnl: Decimal | None = None
    ts: datetime


class Position(BaseModel):
    symbol: str
    market: Market
    qty: Decimal
    avg_cost: Decimal
    currency: str
    last_price: Decimal | None = None
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None


class BalancesOut(BaseModel):
    """按币种展示；pending 是"已卖出、未交收"的在途资金。"""

    available: Decimal
    pending: Decimal
    frozen: Decimal
    total: Decimal


class PendingOut(BaseModel):
    market: Market
    currency: str
    amount: Decimal
    trade_id: str
    release_at: datetime


class AccountSnapshot(BaseModel):
    balances: dict[str, BalancesOut]
    buying_power: dict[str, Decimal]  # key: Market.value
    withdrawable: dict[str, Decimal]  # key: Market.value
    positions: list[Position]
    equity: dict[str, Decimal]  # 按币种：total + 外汇浮动盈亏
    realized_pnl: dict[str, Decimal]
    fees_paid: dict[str, Decimal]
    pending_items: list[PendingOut] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: str
    clock: datetime
    data_sources: dict[str, str]
    provider_status: dict[str, dict] = Field(default_factory=dict)
    symbols: int
    usdhk_rate: Decimal = Decimal("7.80")
    market_data: str = "mock"
    clock_mode: str = "sim"
