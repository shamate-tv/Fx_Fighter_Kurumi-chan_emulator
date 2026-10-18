"""费用引擎：完全由 `MarketRule.fees` 驱动，代码里不硬编码任何费率。"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .enums import FeeKind, Market, Side
from .market_rules import MARKET_RULES, FeeRule
from .models import FeeComponent
from .symbols import SymbolSpec

ZERO = Decimal("0")
CENT = Decimal("0.01")


def to_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class FeeEngine:
    def __init__(self, rules: dict[Market, tuple[FeeRule, ...]] | None = None) -> None:
        # 允许注入自定义规则表（测试/配置覆盖），默认直接读矩阵
        self._rules: dict[Market, tuple[FeeRule, ...]] = (
            dict(rules) if rules is not None else {m: r.fees for m, r in MARKET_RULES.items()}
        )

    def calculate(
        self,
        spec: SymbolSpec,
        side: Side,
        qty: Decimal,
        price: Decimal,
        *,
        closing_qty: Decimal = ZERO,
    ) -> list[FeeComponent]:
        """返回本次成交的费用明细；`closing_qty` 预留给未来的平仓差异化规则。"""
        gross = spec.trade_value(qty, price)
        currency = spec.settlement_currency
        out: list[FeeComponent] = []
        for rule in self._rules.get(spec.market, ()):
            if not rule.applies_to(side):
                continue
            amount = self._amount(rule, gross, qty)
            if rule.value == ZERO:
                # 显式配置为 0 的费率不产出明细，避免成交记录里塞满 0 值
                continue
            out.append(
                FeeComponent(
                    name=rule.name,
                    rate=rule.value,
                    amount=amount,
                    currency=rule.currency or currency,
                )
            )
        return out

    @staticmethod
    def _amount(rule: FeeRule, gross: Decimal, qty: Decimal) -> Decimal:
        if rule.kind is FeeKind.PERCENT:
            raw = gross * rule.value
        elif rule.kind is FeeKind.PER_SHARE:
            raw = qty * rule.value
        else:  # FLAT
            raw = rule.value

        if rule.round_up_to_whole:
            # 港股印花税：向上取整到整元
            raw = raw.to_integral_value(rounding=ROUND_CEILING)
        if rule.min_amount > ZERO and raw < rule.min_amount:
            raw = rule.min_amount
        return to_money(raw)


def total_fees(fees: list[FeeComponent]) -> Decimal:
    return to_money(sum((f.amount for f in fees), ZERO))
