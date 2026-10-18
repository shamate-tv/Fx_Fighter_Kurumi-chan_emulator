"""统一行情 Provider 抽象。

约束（全项目通用）
- 只读行情，绝不调用任何实盘下单接口
- `subscribe` 产出的 tick 必须携带 `stale`；stale 的 tick 不许进入撮合
- 无 key / 无网络时必须能整体降级到 MockProvider，原型始终可运行
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from ..core.enums import Market
from ..core.models import Bar, Quote


class MarketDataProvider(ABC):
    name: str = "base"

    def supports(self, market: Market) -> bool:
        return False

    def status(self) -> dict:
        """供 /api/health 展示：是否可用、当前模式、最近错误（不含任何密钥）。"""
        return {"name": self.name, "enabled": True, "mode": "static"}

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    async def get_history(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]: ...

    @abstractmethod
    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[Quote]: ...

    async def start(self) -> None:  # 可选：建立长连接
        return None

    async def close(self) -> None:
        return None
