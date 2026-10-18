"""基础枚举与领域异常。"""

from __future__ import annotations

from enum import Enum


class Market(str, Enum):
    FX = "FX"
    US = "US"
    HK = "HK"

    @property
    def label(self) -> str:
        return {"FX": "外汇", "US": "美股", "HK": "港股"}[self.value]


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def is_open(self) -> bool:
        return self is OrderStatus.PENDING


class SettlementKind(str, Enum):
    INSTANT = "INSTANT"
    T_N = "T_N"


class LotPolicy(str, Enum):
    FX_UNITS = "FX_UNITS"
    SHARES = "SHARES"
    LOT_TABLE = "LOT_TABLE"


class FeeKind(str, Enum):
    PERCENT = "PERCENT"
    PER_SHARE = "PER_SHARE"
    FLAT = "FLAT"


class ErrorCode(str, Enum):
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    INVALID_QTY = "INVALID_QTY"
    LOT_SIZE_VIOLATION = "LOT_SIZE_VIOLATION"
    INVALID_LIMIT_PRICE = "INVALID_LIMIT_PRICE"
    INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"
    INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"
    SHORTING_NOT_ALLOWED = "SHORTING_NOT_ALLOWED"
    NO_MARKET_DATA = "NO_MARKET_DATA"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    ORDER_NOT_CANCELLABLE = "ORDER_NOT_CANCELLABLE"
    SAME_CURRENCY = "SAME_CURRENCY"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    TIME_CONTROL_UNAVAILABLE = "TIME_CONTROL_UNAVAILABLE"
    SANITY_LOCKED = "SANITY_LOCKED"
    GAME_OVER = "GAME_OVER"


class TradingError(Exception):
    """所有可预期的业务拒绝都抛这个，API 层统一转成错误体。"""

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
