"""API 层：/api/life 快照、sanity 拦截 403、WS 连接下发 life 帧。"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


def make_app():
    settings = Settings(
        market_data="mock",
        use_sim_clock=True,
        initial_cash={"USD": Decimal("0"), "HKD": Decimal("100000")},
        planb_delay_seconds=0,
    )
    return create_app(settings)


def test_life_snapshot_endpoint() -> None:
    app = make_app()
    with TestClient(app) as client:
        response = client.get("/api/life")
        assert response.status_code == 200
        body = response.json()
        assert body["day"] == 1
        assert Decimal(body["cash_jpy"]) == Decimal("100000.00")
        assert Decimal(body["weekly_cost_jpy"]) == Decimal("50000.00")
        assert body["next_settle_on"] is not None
        assert Decimal(body["sanity"]) == Decimal("100.00")
        assert body["caution_level"] == 0
        assert body["status"] == "ALIVE"


def test_low_sanity_rejects_opening_order_with_403() -> None:
    app = make_app()
    with TestClient(app) as client:
        app.state.rt.life.state.sanity = Decimal("10")

        response = client.post(
            "/api/orders",
            json={"symbol": "00700.HK", "side": "BUY", "type": "MARKET", "qty": "100"},
        )

        assert response.status_code == 403
        error = response.json()["error"]
        assert error["code"] == "SANITY_LOCKED"
        assert error["message"] == "精神状态极差，无法开仓"


def test_low_sanity_still_allows_closing_order() -> None:
    app = make_app()
    with TestClient(app) as client:
        buy = client.post(
            "/api/orders",
            json={"symbol": "00700.HK", "side": "BUY", "type": "MARKET", "qty": "100"},
        )
        assert buy.status_code == 200
        assert buy.json()["status"] == "FILLED"

        app.state.rt.life.state.sanity = Decimal("10")
        sell = client.post(
            "/api/orders",
            json={"symbol": "00700.HK", "side": "SELL", "type": "MARKET", "qty": "100"},
        )
        assert sell.status_code == 200


def test_websocket_sends_life_snapshot_on_connect() -> None:
    app = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            types = set()
            for _ in range(30):
                payload = websocket.receive_json()
                types.add(payload.get("type"))
                if "life" in types and "hello" in types:
                    break
            assert "hello" in types
            assert "account" in types
            assert "life" in types


def test_planb_claim_endpoint_and_cooldown() -> None:
    app = make_app()
    with TestClient(app) as client:
        first = client.post("/api/planb/claim", json={"uid": "123456"})
        assert first.status_code == 200
        body = first.json()
        assert body["ok"] is True
        assert Decimal(body["life"]["cash_jpy"]) == Decimal("150000.00")
        assert body["life"]["planb_ready"] is False

        second = client.post("/api/planb/claim", json={"uid": "123456"})
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "PLANB_COOLDOWN"
