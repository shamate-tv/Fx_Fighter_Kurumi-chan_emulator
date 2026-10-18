"""账户 / 订单 / 成交 / 换汇端点。"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..core.enums import ErrorCode, TradingError
from ..core.models import AccountSnapshot, Order, OrderRequest, Position, Trade

router = APIRouter(prefix="/api", tags=["trading"])


class ConvertRequest(BaseModel):
    from_currency: str
    to_currency: str
    amount: Decimal = Field(gt=0)


@router.get("/account", response_model=AccountSnapshot)
async def account(request: Request) -> AccountSnapshot:
    return request.app.state.rt.engine.snapshot()


@router.get("/positions", response_model=list[Position])
async def positions(request: Request) -> list[Position]:
    return request.app.state.rt.engine.snapshot().positions


@router.get("/orders", response_model=list[Order])
async def orders(request: Request, status: str = Query("all", pattern="^(all|open|filled|cancelled|rejected)$")) -> list[Order]:
    return request.app.state.rt.engine.list_orders(status)


@router.post("/orders", response_model=Order)
async def place_order(req: OrderRequest, request: Request) -> Order:
    rt = request.app.state.rt
    # 心态拦截必须在路由层：sanity < 15 时只允许减仓/平仓，开仓/加仓一律 403
    if not rt.life.can_open_position(req):
        raise TradingError(
            ErrorCode.SANITY_LOCKED,
            "精神状态极差，无法开仓",
            {"sanity": str(rt.life.sanity)},
        )
    order, events = await rt.engine.place_order(req)
    await rt.ws.broadcast(events)
    return order


@router.delete("/orders/{order_id}", response_model=Order)
async def cancel_order(order_id: str, request: Request) -> Order:
    rt = request.app.state.rt
    order, events = await rt.engine.cancel_order(order_id)
    await rt.ws.broadcast(events)
    return order


@router.get("/trades", response_model=list[Trade])
async def trades(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[Trade]:
    return request.app.state.rt.engine.list_trades(limit)


@router.post("/cash/convert")
async def convert_currency(req: ConvertRequest, request: Request) -> dict:
    rt = request.app.state.rt
    events = await rt.engine.convert_currency(req.from_currency, req.to_currency, req.amount)
    await rt.ws.broadcast(events)
    return {"ok": True, "account": rt.engine.snapshot().model_dump(mode="python")}
