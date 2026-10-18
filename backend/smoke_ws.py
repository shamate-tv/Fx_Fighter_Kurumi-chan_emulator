"""WS 冒烟：hello → account → tick → 下单 → order/trade/account 推送。"""

import asyncio
import json
import sys

import websockets

BASE = "http://127.0.0.1:8000"


async def collect(ws, seconds: float) -> list[dict]:
    out: list[dict] = []
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - asyncio.get_event_loop().time()))
        except (asyncio.TimeoutError, TimeoutError):
            break
        out.append(json.loads(raw))
    return out


async def main() -> int:
    import httpx

    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=10)
        print("first:", json.loads(first)["type"])

        seen = await collect(ws, 2.5)
        kinds = [m["type"] for m in seen]
        print("boot stream:", sorted(set(kinds)), "count=", len(kinds))

        await ws.send(json.dumps({"type": "subscribe", "symbols": ["AAPL"], "channels": ["tick", "order", "trade", "account"]}))
        sub_ack = await asyncio.wait_for(ws.recv(), timeout=5)
        print("ack:", json.loads(sub_ack)["type"])

        async with httpx.AsyncClient(base_url=BASE, timeout=10) as client:
            resp = await client.post(
                "/api/orders",
                json={"symbol": "AAPL", "side": "BUY", "type": "MARKET", "qty": "1"},
            )
            print("order http:", resp.status_code, resp.json().get("status"))

        after = await collect(ws, 2.0)
        after_kinds = [m["type"] for m in after]
        print("after order:", sorted(set(after_kinds)))

        ticks = [m for m in after if m["type"] == "tick"]
        if ticks:
            t = ticks[-1]
            print("tick sample:", t["symbol"], t["bid"], t["ask"], "stale=", t["stale"])

        accounts = [m for m in after if m["type"] == "account"]
        if accounts:
            acc = accounts[-1]["account"]
            print("account push keys:", sorted(acc.keys()))
            print("buying_power:", acc["buying_power"])

    # 错误响应体
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as client:
        bad = await client.post(
            "/api/orders",
            json={"symbol": "00700.HK", "side": "BUY", "type": "MARKET", "qty": "150"},
        )
        print("lot error:", bad.status_code, json.dumps(bad.json(), ensure_ascii=False))

        unknown = await client.post(
            "/api/orders",
            json={"symbol": "TSLA", "side": "BUY", "type": "MARKET", "qty": "1"},
        )
        print("no-quote symbol:", unknown.status_code, json.dumps(unknown.json(), ensure_ascii=False)[:160])

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
