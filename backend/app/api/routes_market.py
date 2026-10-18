"""行情与市场规则相关端点。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from ..core.enums import Market
from ..core.market_rules import MARKET_RULES
from ..core.models import Bar, HealthOut, Quote
from ..core.symbols import SYMBOLS, symbols_for

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    rt = request.app.state.rt
    return HealthOut(
        status="ok",
        clock=rt.clock.now(),
        data_sources=rt.router.data_sources(),
        provider_status=rt.router.provider_status(),
        symbols=len(SYMBOLS),
        usdhk_rate=rt.settings.usdhk_rate,
        market_data=rt.settings.market_data,
        clock_mode="sim" if rt.settings.use_sim_clock else "real",
    )


@router.get("/markets")
async def markets() -> dict:
    """规则矩阵 + 标的清单：前端据此渲染提示条，不硬编码任何规则。"""
    payload = []
    for market in Market:
        rule = MARKET_RULES[market]
        payload.append(
            {
                "key": market.value,
                "label": market.label,
                "rule": rule.to_public(),
                "symbols": [
                    {
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
                    }
                    for spec in symbols_for(market)
                ],
            }
        )
    return {"markets": payload}


@router.get("/quote/{symbol}", response_model=Quote)
async def quote(symbol: str, request: Request) -> Quote:
    return await request.app.state.rt.router.get_quote(symbol)


@router.get("/history/{symbol}", response_model=list[Bar])
async def history(
    symbol: str,
    request: Request,
    interval: str = Query("1m"),
    limit: int = Query(200, ge=1, le=2000),
) -> list[Bar]:
    return await request.app.state.rt.router.get_history(symbol, interval, limit)


@router.get("/server-time")
async def server_time(request: Request) -> dict:
    now: datetime = request.app.state.rt.clock.now()
    return {"now": now.isoformat(), "utc": datetime.now(timezone.utc).isoformat()}
