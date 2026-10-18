"""WebSocket 端点：行情 / 账户 / 订单 / 成交 / 交收。"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..hub.ws_manager import jsonable

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    rt = websocket.app.state.rt
    # 连接/断开对生存系统的影响由 WsManager.bind_lifecycle 统一处理
    # （服务端主动踢连接时也能正确暂停游戏时间）
    conn = await rt.ws.connect(websocket, rt.router.data_sources(), rt.clock.now())
    try:
        resume = rt.life.consume_resume_info()
        if resume is not None:
            await rt.ws.send_snapshot(conn, {"type": "session_resumed", **resume})
        await rt.ws.send_snapshot(
            conn,
            {
                "type": "account",
                "account": jsonable(rt.engine.snapshot()),
            },
        )
        await rt.ws.send_snapshot(
            conn,
            {
                "type": "life",
                "life": jsonable(rt.life.snapshot()),
            },
        )
        # 新的一局首次连上：补发芽吹推销（每 3 游戏天的定时推送仍由 scheduler 负责）
        for event in await rt.advisor.ensure_first():
            await rt.ws.broadcast_event(event)
        while True:
            payload = await websocket.receive_json()
            if isinstance(payload, dict):
                await rt.ws.handle_message(conn, payload)
    except WebSocketDisconnect:
        rt.ws.disconnect(conn)
    except Exception:  # noqa: BLE001 - 客户端异常断开
        rt.ws.disconnect(conn)
