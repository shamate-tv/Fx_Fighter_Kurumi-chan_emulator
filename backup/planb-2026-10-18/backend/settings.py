"""运行配置：`.env`（python-dotenv）+ 环境变量覆盖，不引入额外配置依赖。

加载顺序：`.env`（不覆盖已存在的环境变量）→ 进程环境变量优先。
密钥只在这里读取，绝不进入代码常量、日志或前端。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_DIR / ".env"

ZERO = Decimal("0")


def load_env_file(path: Path | None = None) -> bool:
    """把 .env 读进进程环境（已有的环境变量优先，不被覆盖）。"""
    target = path or ENV_FILE
    if not target.exists():
        return False
    load_dotenv(target, override=False)
    return True


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_decimal(name: str, default: str) -> Decimal:
    return Decimal(_env(name, default))


@dataclass(frozen=True)
class Settings:
    market_data: str = "mock"  # mock | live
    # 肉鸽开局：交易账户默认 0 日元等值，想交易必须找萌智/黑市借钱
    initial_cash: dict[str, Decimal] = field(
        default_factory=lambda: {
            "USD": Decimal("0"),
            "HKD": Decimal("0"),
        }
    )
    seed: int = 20260302
    tick_interval: float = 0.4
    settlement_scan_interval: float = 5.0
    use_sim_clock: bool = False
    sim_start: datetime = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)
    usdhk_rate: Decimal = Decimal("7.80")
    conversion_spread_bps: Decimal = Decimal("25")

    # ---- 生存系统（游戏时间流速与生活成本） ----
    # TIME_SCALE=60 → 现实 60 秒 = 游戏 1 小时（1 天 = 现实 24 分钟）
    time_scale: float = 60.0
    life_initial_cash_jpy: Decimal = Decimal("100000")
    # 按周结算：房租 + 伙食合计 50000 JPY / 周
    life_rent_jpy: Decimal = Decimal("35000")
    life_food_jpy: Decimal = Decimal("15000")
    life_settle_days: int = 7
    usdjpy_fallback: Decimal = Decimal("150")
    sanity_max: Decimal = Decimal("100")
    sanity_open_threshold: Decimal = Decimal("15")
    maintenance_margin_ratio: Decimal = Decimal("0.5")

    # ---- Plan B：B站求关注换生活费 ----
    planb_reward_jpy: Decimal = Decimal("50000")
    planb_cooldown_seconds: int = 86400
    planb_delay_seconds: float = 0.6  # 模拟"网络请求 + 验证 UID"的延迟

    # ---- 萌智子事件：每 N 个游戏日出现一次 ----
    advisor_interval_days: int = 3

    # 数据源
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_feed: str = "iex"
    biquote_enabled: bool = True
    hk_provider: str = "mock"
    quote_stale_after: float = 180.0

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_api_key.strip() and self.alpaca_secret_key.strip())

    @property
    def live_enabled(self) -> bool:
        return self.market_data.strip().lower() == "live"

    def describe_sources(self) -> dict[str, str]:
        """只描述配置意图，不暴露任何密钥。"""
        live = self.live_enabled
        return {
            "FX": "biquote" if (live and self.biquote_enabled) else "mock",
            "US": "alpaca" if (live and self.alpaca_configured) else "mock",
            "HK": "mock",
        }


def load_settings() -> Settings:
    load_env_file()
    return Settings(
        market_data=_env("MARKET_DATA", "mock"),
        initial_cash={
            "USD": _env_decimal("INITIAL_USD", "0"),
            "HKD": _env_decimal("INITIAL_HKD", "0"),
        },
        seed=int(_env("MOCK_SEED", "20260302")),
        tick_interval=float(_env("MOCK_TICK_INTERVAL", "0.4")),
        settlement_scan_interval=float(_env("SETTLEMENT_SCAN_INTERVAL", "5")),
        use_sim_clock=_env_bool("USE_SIM_CLOCK", False),
        usdhk_rate=_env_decimal("USDHKD_RATE", "7.80"),
        conversion_spread_bps=_env_decimal("CONVERSION_SPREAD_BPS", "25"),
        time_scale=float(_env("TIME_SCALE", "60")),
        life_initial_cash_jpy=_env_decimal("LIFE_INITIAL_JPY", "100000"),
        life_rent_jpy=_env_decimal("LIFE_RENT_JPY", "35000"),
        life_food_jpy=_env_decimal("LIFE_FOOD_JPY", "15000"),
        life_settle_days=int(_env("LIFE_SETTLE_DAYS", "7")),
        usdjpy_fallback=_env_decimal("USDJPY_FALLBACK", "150"),
        sanity_open_threshold=_env_decimal("SANITY_OPEN_THRESHOLD", "15"),
        maintenance_margin_ratio=_env_decimal("MAINTENANCE_MARGIN_RATIO", "0.5"),
        planb_reward_jpy=_env_decimal("PLANB_REWARD_JPY", "50000"),
        planb_cooldown_seconds=int(_env("PLANB_COOLDOWN_SECONDS", "86400")),
        planb_delay_seconds=float(_env("PLANB_DELAY_SECONDS", "0.6")),
        advisor_interval_days=int(_env("ADVISOR_INTERVAL_DAYS", "3")),
        alpaca_api_key=_env("ALPACA_API_KEY", ""),
        alpaca_secret_key=_env("ALPACA_SECRET_KEY", ""),
        alpaca_feed=_env("ALPACA_FEED", "iex"),
        biquote_enabled=_env_bool("BIQUOTE_ENABLED", True),
        hk_provider=_env("HK_PROVIDER", "mock"),
        quote_stale_after=float(_env("QUOTE_STALE_AFTER", "180")),
    )
