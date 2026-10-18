"""应用装配：所有依赖在这里接线，测试可以单独复用 `build_runtime`。"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import routes_admin, routes_game, routes_market, routes_trading, routes_ws
from .core.clock import Clock, RealClock, SimClock
from .core.enums import ErrorCode, Market, TradingError
from .engine.account import Account
from .engine.trading_engine import TradingEngine
from .game.clock import GameClock
from .game.life_engine import LifeEngine
from .game.scheduler import LifeScheduler
from .game.advisor import AdvisorEngine
from .hub.market_hub import MarketHub
from .hub.ws_manager import WsManager
from .providers.alpaca import AlpacaProvider
from .providers.base import MarketDataProvider
from .providers.biquote import BiquoteProvider
from .providers.mock import MockProvider
from .providers.router import ProviderRouter
from .settings import Settings, load_settings

log = logging.getLogger("trading.app")


def _configure_logging() -> None:
    """让 trading.* 的日志能在 uvicorn 下可见（不重复装配已有的 handler）。"""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        )
    logging.getLogger("trading").setLevel(logging.INFO)


@dataclass
class Runtime:
    settings: Settings
    clock: Clock
    account: Account
    engine: TradingEngine
    mock: MockProvider
    providers: dict[Market, MarketDataProvider]
    router: ProviderRouter
    hub: MarketHub
    ws: WsManager
    life: LifeEngine
    scheduler: LifeScheduler
    advisor: AdvisorEngine


def build_providers(settings: Settings, mock: MockProvider) -> dict[Market, MarketDataProvider]:
    """按配置装配真实数据源；任何一路缺 key / 不可用都回退 Mock，保证原型始终可运行。"""
    if not settings.live_enabled:
        log.info("MARKET_DATA=mock：三市场全部使用本地随机游走")
        return {market: mock for market in Market}

    providers: dict[Market, MarketDataProvider] = {}

    if settings.biquote_enabled:
        providers[Market.FX] = BiquoteProvider(fallback=mock, stale_after=settings.quote_stale_after)
        log.info("外汇使用 biquote：SignalR 推流 → REST 轮询 → mock 兜底")
    else:
        providers[Market.FX] = mock

    if settings.alpaca_configured:
        providers[Market.US] = AlpacaProvider(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            feed=settings.alpaca_feed,
            fallback=mock,
            stale_after=max(settings.quote_stale_after, 300.0),
        )
        log.info("美股使用 Alpaca %s 行情流（只读行情，不下单）", settings.alpaca_feed)
    else:
        log.warning("未配置 ALPACA_API_KEY / ALPACA_SECRET_KEY，美股回退 MockProvider")
        providers[Market.US] = mock

    # 港股真实源留到 Phase 4，当前固定 Mock
    providers[Market.HK] = mock
    return providers


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or load_settings()

    clock: Clock = SimClock(settings.sim_start) if settings.use_sim_clock else RealClock()
    account = Account(settings.initial_cash)
    engine = TradingEngine(
        account,
        clock,
        usdhk_rate=settings.usdhk_rate,
        conversion_spread_bps=settings.conversion_spread_bps,
    )

    mock = MockProvider(seed=settings.seed, tick_interval=settings.tick_interval)
    providers = build_providers(settings, mock)
    router = ProviderRouter(providers, fallback=mock)

    hub = MarketHub(
        router,
        engine,
        clock,
        settlement_scan_interval=settings.settlement_scan_interval,
    )
    ws = WsManager()
    game_clock = GameClock(clock, settings.time_scale)

    def _usdjpy() -> object:
        """汇率穿透：欠款按真实 USDJPY 最新报价折算。"""
        quote = hub.last_quotes.get("USDJPY")
        return quote.mid if quote is not None else None

    life = LifeEngine(
        account=account,
        engine=engine,
        clock=game_clock,
        settings=settings,
        usdjpy_provider=_usdjpy,
    )
    advisor = AdvisorEngine(life, settings)
    scheduler = LifeScheduler(life, ws, interval_seconds=1.0, event_engine=advisor)
    life.attach_scheduler(scheduler)

    return Runtime(
        settings=settings,
        clock=clock,
        account=account,
        engine=engine,
        mock=mock,
        providers=providers,
        router=router,
        hub=hub,
        ws=ws,
        life=life,
        scheduler=scheduler,
        advisor=advisor,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    _configure_logging()
    runtime = build_runtime(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.hub.add_sink(runtime.ws.broadcast_event)
        runtime.hub.add_sink(runtime.life.on_event)
        runtime.ws.bind_lifecycle(runtime.life.on_client_connected, runtime.life.on_client_disconnected)
        runtime.scheduler.start()
        runtime.life.start_offline()  # 没有玩家在线，游戏时间默认暂停
        await runtime.hub.start()
        try:
            yield
        finally:
            runtime.scheduler.shutdown()
            await runtime.hub.close()

    app = FastAPI(
        title="多市场 T+0 模拟交易原型",
        description="外汇 / 美股 / 港股本地模拟交易，无实盘下单。",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.rt = runtime
    app.state.settings = runtime.settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes_market.router)
    app.include_router(routes_trading.router)
    app.include_router(routes_game.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_ws.router)

    @app.exception_handler(TradingError)
    async def _trading_error_handler(_: Request, exc: TradingError) -> JSONResponse:
        if exc.code is ErrorCode.SANITY_LOCKED:
            status_code = 403
        elif exc.code is ErrorCode.PLANB_COOLDOWN:
            status_code = 429
        elif exc.code is ErrorCode.GAME_OVER:
            status_code = 409
        else:
            status_code = 400
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code.value, "message": exc.message, "details": exc.details}},
        )

    return app


app = create_app()
