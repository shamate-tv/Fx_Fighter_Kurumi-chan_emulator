"""市场规则矩阵：按 `SymbolSpec.market` 索引，是撮合/费用/交收的唯一规则来源。

对照关系（与用户给定的矩阵逐项对应）
  交易制度      trade_mode             三市场统一 T0
  交收          settlement / settlement_days       FX 即时 / US T+1 / HK T+2
  提现          sell_proceeds_release   FX instant / US settle / HK settle
  同市场再投资  allow_same_market_reinvest         三市场 True
  跨市场再投资  cross_market_reinvest             FX instant / US after_settlement / HK after_settlement
  最小单位      lot_policy + allowed_lot_sizes     FX 手 / US 1 股 / HK 手数表
  涨跌幅        price_limit + flags                原型只做规则位，不改撮合
  摩擦成本      fees
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .enums import FeeKind, LotPolicy, Market, SettlementKind, Side

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class FeeRule:
    name: str
    kind: FeeKind
    value: Decimal
    currency: str | None = None  # None = 跟随标的结算币种
    side: Side | None = None  # None = 买卖双边
    min_amount: Decimal = ZERO
    round_up_to_whole: bool = False

    def applies_to(self, side: Side) -> bool:
        return self.side is None or self.side is side


@dataclass(frozen=True, slots=True)
class MarketRule:
    market: Market
    trade_mode: str
    settlement: SettlementKind
    settlement_days: int
    sell_proceeds_release: str
    allow_same_market_reinvest: bool
    cross_market_reinvest: str
    lot_policy: LotPolicy
    allowed_lot_sizes: frozenset[int] | None
    price_limit: str
    flags: dict[str, bool] = field(default_factory=dict)
    fees: tuple[FeeRule, ...] = ()

    # 供 API 直接序列化给前端（规则提示条从后端渲染，前端不硬编码）
    def to_public(self) -> dict:
        return {
            "market": self.market.value,
            "label": self.market.label,
            "trade_mode": self.trade_mode,
            "settlement": self.settlement.value,
            "settlement_days": self.settlement_days,
            "sell_proceeds_release": self.sell_proceeds_release,
            "allow_same_market_reinvest": self.allow_same_market_reinvest,
            "cross_market_reinvest": self.cross_market_reinvest,
            "lot_policy": self.lot_policy.value,
            "allowed_lot_sizes": sorted(self.allowed_lot_sizes) if self.allowed_lot_sizes else None,
            "price_limit": self.price_limit,
            "flags": dict(self.flags),
            "fees": [
                {
                    "name": f.name,
                    "kind": f.kind.value,
                    "value": str(f.value),
                    "side": f.side.value if f.side else "BOTH",
                    "min_amount": str(f.min_amount),
                    "round_up_to_whole": f.round_up_to_whole,
                }
                for f in self.fees
            ],
        }


_LOT_SIZES = frozenset({1, 50, 100, 500, 1000, 2000, 5000, 10000})

MARKET_RULES: dict[Market, MarketRule] = {
    # 外汇：T+0 + 即时交收 + CFD 保证金逻辑，成本体现在点差
    Market.FX: MarketRule(
        market=Market.FX,
        trade_mode="T0",
        settlement=SettlementKind.INSTANT,
        settlement_days=0,
        sell_proceeds_release="instant",
        allow_same_market_reinvest=True,
        cross_market_reinvest="instant",
        lot_policy=LotPolicy.FX_UNITS,
        allowed_lot_sizes=None,
        price_limit="NONE",
        flags={"margin_trading": True, "short_selling": True, "cost_is_spread": True},
        fees=(),  # 点差即成本，不另收佣金
    ),
    # 美股：T+0 交易、T+1 交收；无个股涨跌停，熔断/波动限制先做标志位
    Market.US: MarketRule(
        market=Market.US,
        trade_mode="T0",
        settlement=SettlementKind.T_N,
        settlement_days=1,
        sell_proceeds_release="settle",
        allow_same_market_reinvest=True,
        cross_market_reinvest="after_settlement",
        lot_policy=LotPolicy.SHARES,
        allowed_lot_sizes=None,
        price_limit="NONE",
        flags={"market_wide_circuit_breaker": True, "individual_volatility_halt": True},
        fees=(
            # 佣金可设 0；未来要开就改 value
            FeeRule(name="commission", kind=FeeKind.PERCENT, value=Decimal("0"), side=None),
        ),
    ),
    # 港股：T+0 交易、T+2 交收；四项固定费用双边收取
    Market.HK: MarketRule(
        market=Market.HK,
        trade_mode="T0",
        settlement=SettlementKind.T_N,
        settlement_days=2,
        sell_proceeds_release="settle",
        allow_same_market_reinvest=True,
        cross_market_reinvest="after_settlement",
        lot_policy=LotPolicy.LOT_TABLE,
        allowed_lot_sizes=_LOT_SIZES,
        price_limit="NONE",
        flags={"vcm_cooling_off": True},
        fees=(
            # 印花税 0.1%，双边，向上取整到整元（不足 1 元按 1 元）
            FeeRule(
                name="stamp_duty",
                kind=FeeKind.PERCENT,
                value=Decimal("0.001"),
                side=None,
                min_amount=Decimal("1"),
                round_up_to_whole=True,
            ),
            # 交易征费 0.0027%
            FeeRule(name="transaction_levy", kind=FeeKind.PERCENT, value=Decimal("0.000027")),
            # 交易费 0.00565%
            FeeRule(name="trading_fee", kind=FeeKind.PERCENT, value=Decimal("0.0000565")),
            # 结算费 0.0042%
            FeeRule(name="settlement_fee", kind=FeeKind.PERCENT, value=Decimal("0.000042")),
            # 佣金可选（默认 0）
            FeeRule(name="commission", kind=FeeKind.PERCENT, value=Decimal("0")),
        ),
    ),
}


def rule_for(market: Market) -> MarketRule:
    return MARKET_RULES[market]
