"""生存系统端点：玩家状态快照、Plan B（B站求关注）。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..game.events import LifeEvent
from ..game.models import LifeOut

router = APIRouter(prefix="/api", tags=["game"])


class PlanBRequest(BaseModel):
    uid: str | None = None  # 模拟 B 站 UID 校验；不传则跳过校验


@router.get("/life", response_model=LifeOut)
async def life(request: Request) -> LifeOut:
    return request.app.state.rt.life.snapshot()


@router.post("/planb/claim")
async def claim_planb(req: PlanBRequest, request: Request) -> dict:
    """久留美的 Plan B：去 B 站骗几个粉丝关注，换 50000 日元生活费。

    校验通过后进 24 小时冷却；冷却中再次请求返回 429 + 剩余秒数。
    """
    rt = request.app.state.rt
    result = await rt.life.claim_planb(uid=req.uid)
    await rt.ws.broadcast([LifeEvent(rt.engine.clock.now(), rt.life.snapshot())])
    return result
