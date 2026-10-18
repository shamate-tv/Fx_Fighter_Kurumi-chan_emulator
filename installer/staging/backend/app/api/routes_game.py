"""生存系统端点：玩家状态快照。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..game.models import LifeOut

router = APIRouter(prefix="/api", tags=["game"])


@router.get("/life", response_model=LifeOut)
async def life(request: Request) -> LifeOut:
    return request.app.state.rt.life.snapshot()
