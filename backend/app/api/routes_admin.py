"""管理端点：重置账户、模拟时钟快进。"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..core.clock import SimClock
from ..core.enums import ErrorCode, Market, TradingError
from ..core.symbols import make_custom_spec, register_custom_spec

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdvanceRequest(BaseModel):
    days: float = 0
    hours: float = 0
    minutes: float = 0


class AddSymbolRequest(BaseModel):
    """自定义标的：用户只需给代码 / 市场 / 参考价，其余按市场规则矩阵补默认值。"""

    symbol: str
    market: Market
    description: str = ""
    start_price: Decimal = Field(gt=0)
    lot_size: int | None = None
    price_precision: int | None = None
    spread: Decimal | None = Field(default=None, gt=0)


class LifeCashRequest(BaseModel):
    cash_jpy: Decimal


@router.post("/reset")
async def reset(request: Request) -> dict:
    rt = request.app.state.rt
    await rt.engine.reset(rt.settings.initial_cash)
    await rt.life.reset()
    rt.advisor.reset()
    return {"ok": True, "account": rt.engine.snapshot().model_dump(mode="python")}


@router.post("/advisor")
async def advisor(request: Request) -> dict:
    """调试/演示：立即推送一条芽吹"全仓梭哈"事件。"""
    rt = request.app.state.rt
    events = await rt.advisor.trigger()
    for event in events:
        await rt.ws.broadcast_event(event)
    return {"ok": True, "pushed": len(events)}


@router.post("/life-settle")
async def life_settle(request: Request) -> dict:
    """调试/演示：立即结算一周房租（用于快速看到天台结局与重开按钮）。"""
    rt = request.app.state.rt
    events = await rt.life.force_settle()
    for event in events:
        await rt.ws.broadcast_event(event)
    return {"ok": True, "events": len(events), "life": rt.life.snapshot().model_dump(mode="python")}


@router.post("/life-cash")
async def life_cash(req: LifeCashRequest, request: Request) -> dict:
    """调试/演示：直接设置生活现金（配合 /life-settle 可几秒内造出天台结局）。"""
    rt = request.app.state.rt
    rt.life.state.cash_jpy = req.cash_jpy
    return {"ok": True, "life": rt.life.snapshot().model_dump(mode="python")}


@router.post("/advance-time")
async def advance_time(req: AdvanceRequest, request: Request) -> dict:
    rt = request.app.state.rt
    clock = rt.clock
    if not isinstance(clock, SimClock):
        raise TradingError(ErrorCode.TIME_CONTROL_UNAVAILABLE, "当前使用真实时钟，无法快进", {})
    delta = timedelta(days=req.days, hours=req.hours, minutes=req.minutes)
    now = clock.advance(delta)
    # 快进后立即结算一次，让"T+1/T+2 到账"立刻可见
    events = await rt.engine.on_timer(now)
    await rt.ws.broadcast(events)
    return {"ok": True, "now": now.isoformat(), "released_events": len(events)}


@router.post("/symbols")
async def add_symbol(req: AddSymbolRequest, request: Request) -> dict:
    """注册自定义标的（运行期有效，重启后重置；行情由 MockProvider 生成）。"""
    rt = request.app.state.rt
    spec = make_custom_spec(
        symbol=req.symbol,
        market=req.market,
        description=req.description,
        start_price=req.start_price,
        lot_size=req.lot_size,
        price_precision=req.price_precision,
        spread=req.spread,
    )
    # 先接进 mock 行情与注册表，再让 hub 预热并开始推流
    rt.mock.add_spec(spec)
    register_custom_spec(spec)
    await rt.hub.add_symbol(spec.symbol)

    return {
        "ok": True,
        "symbol": {
            "symbol": spec.symbol,
            "description": spec.description,
            "market": spec.market.value,
            "currency": spec.settlement_currency,
            "quote_currency": spec.quote_currency,
            "lot_size": spec.lot_size,
            "min_qty": str(spec.min_qty),
            "qty_step": str(spec.qty_step),
            "qty_unit": spec.qty_unit.strip(),
            "price_precision": spec.price_precision,
            "contract_size": spec.contract_size,
            "leverage": str(spec.leverage) if spec.leverage else None,
        },
    }
