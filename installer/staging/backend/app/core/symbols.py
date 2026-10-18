"""标的注册表：每个标的的 market 字段是市场规则矩阵的唯一索引入口。

设计要点
- `market` 决定适用哪条 `MarketRule`（撮合/费用/交收全靠它，代码里不出现 if 市场==... 的分支散落）
- 港股 `lot_size` 受限于 {1,50,100,500,1000,2000,5000,10000}，构造时强校验
- 美股允许 1 股（含零碎股规则由 `qty_step=1` 表达）
- 外汇以"手"计量，1 手 = `contract_size` 单位，最小 0.01 手
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .enums import ErrorCode, LotPolicy, Market, TradingError

ZERO = Decimal("0")

# 港股每手股数的合法取值（用户明确要求：取 1/50/100/500/1000/2000/5000/10000 之一）
HK_ALLOWED_LOT_SIZES: frozenset[int] = frozenset({1, 50, 100, 500, 1000, 2000, 5000, 10000})


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    symbol: str
    market: Market
    description: str
    quote_currency: str
    settlement_currency: str
    min_qty: Decimal
    qty_step: Decimal
    price_precision: int
    lot_size: int | None
    contract_size: Decimal
    start_price: Decimal
    sigma_bps: Decimal
    spread: Decimal
    leverage: Decimal | None = None
    shortable: bool = False

    # ---- 数量校验 -------------------------------------------------------
    def validate_qty(self, qty: Decimal) -> None:
        if qty is None or qty <= ZERO:
            raise TradingError(ErrorCode.INVALID_QTY, f"{self.symbol} 数量必须为正数", {"qty": str(qty)})
        if qty < self.min_qty:
            raise TradingError(
                ErrorCode.INVALID_QTY,
                f"{self.symbol} 最小下单量 {self.min_qty}{self.qty_unit}",
                {"qty": str(qty), "min_qty": str(self.min_qty)},
            )
        if (qty - self.min_qty) % self.qty_step != ZERO:
            raise TradingError(
                ErrorCode.LOT_SIZE_VIOLATION,
                f"{self.symbol} 下单量必须是 {self.qty_step}{self.qty_unit} 的整数倍"
                + (f"（每手 {self.lot_size} 股）" if self.lot_size else ""),
                {"qty": str(qty), "qty_step": str(self.qty_step), "lot_size": self.lot_size},
            )

    # ---- 价格 -----------------------------------------------------------
    def normalize_price(self, price: Decimal) -> Decimal:
        return price.quantize(Decimal(1).scaleb(-self.price_precision))

    def validate_price(self, price: Decimal) -> None:
        if price is None or price <= ZERO:
            raise TradingError(ErrorCode.INVALID_LIMIT_PRICE, f"{self.symbol} 限价必须为正数")

    # ---- 数值换算 -------------------------------------------------------
    @property
    def qty_unit(self) -> str:
        if self.market is Market.FX:
            return " 手"
        return " 股"

    @property
    def lot_policy(self) -> LotPolicy:
        if self.market is Market.FX:
            return LotPolicy.FX_UNITS
        if self.market is Market.HK:
            return LotPolicy.LOT_TABLE
        return LotPolicy.SHARES

    def units(self, qty: Decimal) -> Decimal:
        """外汇换算成基础货币单位数；股票就是股数。"""
        if self.market is Market.FX:
            return qty * self.contract_size
        return qty

    def trade_value(self, qty: Decimal, price: Decimal) -> Decimal:
        """成交名义金额，统一折算到 settlement_currency。

        - 股票：股数 × 价格
        - 外汇且报价币种 == 结算币种：单位数 × 价格
        - 外汇且报价币种 != 结算币种（如 USDJPY）：单位数 ÷ 价格
        """
        if self.market is Market.FX:
            base = self.units(qty) * price
            if self.quote_currency == self.settlement_currency:
                return base
            return self.units(qty) / price
        return qty * price

    def required_margin(self, qty: Decimal, price: Decimal) -> Decimal:
        """外汇保证金；股票无杠杆，返回 0。"""
        if self.leverage is None:
            return ZERO
        return self.trade_value(qty, price) / self.leverage


# ---------------------------------------------------------------------------
# 内置标的（约 20 个，三市场全覆盖；港股 lot_size 全部落在合法集合内）
# ---------------------------------------------------------------------------

def _fx(
    symbol: str,
    desc: str,
    price: str,
    spread: str,
    quote: str = "USD",
    precision: int = 5,
    contract: str = "100000",
    sigma_bps: str = "0.5",
) -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol,
        market=Market.FX,
        description=desc,
        quote_currency=quote,
        settlement_currency="USD",
        min_qty=Decimal("0.01"),
        qty_step=Decimal("0.01"),
        price_precision=precision,
        lot_size=None,
        contract_size=Decimal(contract),
        start_price=Decimal(price),
        sigma_bps=Decimal(sigma_bps),
        spread=Decimal(spread),
        leverage=Decimal("20"),
        shortable=True,
    )


def _us(symbol: str, desc: str, price: str, spread: str, sigma_bps: str = "3") -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol,
        market=Market.US,
        description=desc,
        quote_currency="USD",
        settlement_currency="USD",
        min_qty=Decimal("1"),
        qty_step=Decimal("1"),
        price_precision=2,
        lot_size=1,
        contract_size=Decimal("1"),
        start_price=Decimal(price),
        sigma_bps=Decimal(sigma_bps),
        spread=Decimal(spread),
        leverage=None,
        shortable=False,
    )


def _hk(symbol: str, desc: str, price: str, lot_size: int, spread: str, sigma_bps: str = "4") -> SymbolSpec:
    if lot_size not in HK_ALLOWED_LOT_SIZES:
        raise ValueError(f"{symbol} lot_size={lot_size} 不在港股合法集合 {sorted(HK_ALLOWED_LOT_SIZES)} 内")
    return SymbolSpec(
        symbol=symbol,
        market=Market.HK,
        description=desc,
        quote_currency="HKD",
        settlement_currency="HKD",
        min_qty=Decimal(lot_size),
        qty_step=Decimal(lot_size),
        price_precision=2,
        lot_size=lot_size,
        contract_size=Decimal("1"),
        start_price=Decimal(price),
        sigma_bps=Decimal(sigma_bps),
        spread=Decimal(spread),
        leverage=None,
        shortable=False,
    )


_BUILTIN: tuple[SymbolSpec, ...] = (
    # --- 外汇：T+0，即时交收，点差是主要成本 ---
    _fx("EURUSD", "欧元 / 美元", "1.0850", "0.00008"),
    _fx("GBPUSD", "英镑 / 美元", "1.2700", "0.00012"),
    _fx("AUDUSD", "澳元 / 美元", "0.6520", "0.00010"),
    _fx("USDJPY", "美元 / 日元", "149.500", "0.008", quote="JPY", precision=3),
    _fx("USDCHF", "美元 / 瑞郎", "0.8850", "0.00010", quote="CHF"),
    _fx("XAUUSD", "现货黄金 / 美元", "2385.00", "0.35", precision=2, contract="100", sigma_bps="2"),
    # --- 美股：T+0 交易 / T+1 交收，允许 1 股 ---
    _us("AAPL", "苹果", "228.50", "0.02"),
    _us("MSFT", "微软", "421.30", "0.03"),
    _us("NVDA", "英伟达", "135.80", "0.02", sigma_bps="5"),
    _us("TSLA", "特斯拉", "248.60", "0.03", sigma_bps="6"),
    _us("AMZN", "亚马逊", "186.40", "0.02"),
    _us("SPY", "标普 500 ETF", "565.20", "0.02", sigma_bps="1.5"),
    # --- 港股：T+0 交易 / T+2 交收，按 lot_size 取整 ---
    _hk("00700.HK", "腾讯控股", "380.00", 100, "0.20"),
    _hk("09988.HK", "阿里巴巴-W", "82.50", 100, "0.05"),
    _hk("00941.HK", "中国移动", "72.80", 500, "0.10"),
    _hk("00883.HK", "中国海洋石油", "18.64", 1000, "0.02"),
    _hk("02318.HK", "中国平安", "42.30", 500, "0.05"),
    _hk("00016.HK", "新鸿基地产", "78.90", 1000, "0.10"),
)

SYMBOLS: dict[str, SymbolSpec] = {spec.symbol: spec for spec in _BUILTIN}


def get_spec(symbol: str) -> SymbolSpec:
    spec = SYMBOLS.get(symbol.upper() if symbol.isalpha() else symbol)
    if spec is None:
        raise TradingError(ErrorCode.UNKNOWN_SYMBOL, f"未知标的：{symbol}", {"symbol": symbol})
    return spec


def symbols_for(market: Market) -> list[SymbolSpec]:
    # 用 SYMBOLS（而不是 _BUILTIN），这样运行期注册的自定义标的也能出现在 /api/markets 里
    return [s for s in SYMBOLS.values() if s.market is market]


# ---------------------------------------------------------------------------
# 自定义标的：运行期通过 POST /api/symbols 注册
# 约束：自定义标的一律走 MockProvider 报价（真实数据源只认自己的白名单标的）
# ---------------------------------------------------------------------------

_CUSTOM: dict[str, SymbolSpec] = {}


def is_custom(symbol: str) -> bool:
    return symbol in _CUSTOM


def custom_symbols() -> list[SymbolSpec]:
    return list(_CUSTOM.values())


def normalize_symbol(symbol: str, market: Market) -> str:
    """把用户输入的代码规范成注册表键：美股/外汇转大写，港股补成 5 位 + .HK。"""
    raw = symbol.strip().upper()
    if not raw:
        raise TradingError(ErrorCode.UNKNOWN_SYMBOL, "股票代码不能为空", {"symbol": symbol})

    if market is Market.HK:
        body = raw[:-3] if raw.endswith(".HK") else raw
        if not body.isdigit():
            raise TradingError(
                ErrorCode.UNKNOWN_SYMBOL,
                f"港股代码必须是数字（可带 .HK），收到 {symbol}",
                {"symbol": symbol},
            )
        return f"{int(body):05d}.HK"

    if market is Market.FX:
        if not raw.isalpha() or len(raw) != 6:
            raise TradingError(
                ErrorCode.UNKNOWN_SYMBOL,
                f"外汇代码必须是 6 位字母（如 USDCNH），收到 {symbol}",
                {"symbol": symbol},
            )
        return raw

    # 美股：允许字母与一个点号后缀（如 BRK.B / BF.B）
    if not raw.replace(".", "").isalpha() or raw.count(".") > 1 or raw.startswith(".") or raw.endswith("."):
        raise TradingError(
            ErrorCode.UNKNOWN_SYMBOL,
            f"美股代码只能是字母（可带点号，如 BRK.B），收到 {symbol}",
            {"symbol": symbol},
        )
    if len(raw) > 12:
        raise TradingError(ErrorCode.UNKNOWN_SYMBOL, f"股票代码过长：{symbol}", {"symbol": symbol})
    return raw


def make_custom_spec(
    *,
    symbol: str,
    market: Market,
    description: str,
    start_price: Decimal,
    lot_size: int | None = None,
    price_precision: int | None = None,
    spread: Decimal | None = None,
) -> SymbolSpec:
    """按市场规则矩阵给自定义代码套一套默认参数（用户只提供代码/市场/参考价即可）。"""
    code = normalize_symbol(symbol, market)
    if code in SYMBOLS:
        raise TradingError(ErrorCode.UNKNOWN_SYMBOL, f"标的 {code} 已存在", {"symbol": code})
    if start_price is None or start_price <= ZERO:
        raise TradingError(ErrorCode.INVALID_QTY, "参考价必须为正数", {"start_price": str(start_price)})

    desc = (description or "").strip() or code

    if market is Market.FX:
        quote_currency = "USD"
        if code.startswith("USD") and len(code) == 6:
            quote_currency = code[3:]
        precision = price_precision or 5
        spec = SymbolSpec(
            symbol=code,
            market=market,
            description=desc,
            quote_currency=quote_currency,
            settlement_currency="USD",
            min_qty=Decimal("0.01"),
            qty_step=Decimal("0.01"),
            price_precision=precision,
            lot_size=None,
            contract_size=Decimal("100000"),
            start_price=start_price,
            sigma_bps=Decimal("0.5"),
            spread=spread or max(start_price * Decimal("0.00008"), Decimal(1).scaleb(-precision)),
            leverage=Decimal("20"),
            shortable=True,
        )
    elif market is Market.US:
        precision = price_precision or 2
        spec = SymbolSpec(
            symbol=code,
            market=market,
            description=desc,
            quote_currency="USD",
            settlement_currency="USD",
            min_qty=Decimal("1"),
            qty_step=Decimal("1"),
            price_precision=precision,
            lot_size=1,
            contract_size=Decimal("1"),
            start_price=start_price,
            sigma_bps=Decimal("3"),
            spread=spread or Decimal("0.01"),
            leverage=None,
            shortable=False,
        )
    else:  # HK
        lot = lot_size or 100
        if lot not in HK_ALLOWED_LOT_SIZES:
            raise TradingError(
                ErrorCode.INVALID_QTY,
                f"港股每手股数必须是 {sorted(HK_ALLOWED_LOT_SIZES)} 之一，收到 {lot}",
                {"lot_size": lot},
            )
        precision = price_precision or 2
        spec = SymbolSpec(
            symbol=code,
            market=market,
            description=desc,
            quote_currency="HKD",
            settlement_currency="HKD",
            min_qty=Decimal(lot),
            qty_step=Decimal(lot),
            price_precision=precision,
            lot_size=lot,
            contract_size=Decimal("1"),
            start_price=start_price,
            sigma_bps=Decimal("4"),
            spread=spread or Decimal("0.01"),
            leverage=None,
            shortable=False,
        )
    return spec


def register_custom_spec(spec: SymbolSpec) -> None:
    SYMBOLS[spec.symbol] = spec
    _CUSTOM[spec.symbol] = spec
