"""费用引擎：三市场按矩阵扣费。"""

from __future__ import annotations

from decimal import Decimal

from app.core.enums import Side
from app.core.fees import FeeEngine
from app.core.symbols import SYMBOLS

ENGINE = FeeEngine()


def _by_name(fees) -> dict[str, Decimal]:
    return {f.name: f.amount for f in fees}


def test_hk_four_fees_on_100k_contract() -> None:
    spec = SYMBOLS["00700.HK"]
    fees = ENGINE.calculate(spec, Side.BUY, Decimal("100"), Decimal("1000"))
    by_name = _by_name(fees)

    # 成交额 100,000 HKD
    assert by_name["stamp_duty"] == Decimal("100.00")  # 0.1% 双边
    assert by_name["transaction_levy"] == Decimal("2.70")  # 0.0027%
    assert by_name["trading_fee"] == Decimal("5.65")  # 0.00565%
    assert by_name["settlement_fee"] == Decimal("4.20")  # 0.0042%
    assert sum(by_name.values()) == Decimal("112.55")


def test_hk_stamp_duty_rounds_up_to_whole_dollar() -> None:
    spec = SYMBOLS["00700.HK"]
    # 100,050 × 0.1% = 100.05 → 向上取整 101
    fees = ENGINE.calculate(spec, Side.BUY, Decimal("100"), Decimal("1000.5"))
    assert _by_name(fees)["stamp_duty"] == Decimal("101.00")


def test_hk_fees_are_symmetric_buy_and_sell() -> None:
    spec = SYMBOLS["00700.HK"]
    buy = ENGINE.calculate(spec, Side.BUY, Decimal("500"), Decimal("72.80"))
    sell = ENGINE.calculate(spec, Side.SELL, Decimal("500"), Decimal("72.80"))
    assert _by_name(buy) == _by_name(sell)


def test_us_commission_zero_produces_no_fee_lines() -> None:
    spec = SYMBOLS["AAPL"]
    fees = ENGINE.calculate(spec, Side.BUY, Decimal("10"), Decimal("228.50"))
    assert fees == []


def test_fx_has_no_explicit_fees_cost_is_spread() -> None:
    spec = SYMBOLS["EURUSD"]
    fees = ENGINE.calculate(spec, Side.BUY, Decimal("1"), Decimal("1.0850"))
    assert fees == []


def test_fee_currency_follows_symbol_settlement_currency() -> None:
    hk = ENGINE.calculate(SYMBOLS["00700.HK"], Side.BUY, Decimal("100"), Decimal("380"))
    assert all(f.currency == "HKD" for f in hk)
