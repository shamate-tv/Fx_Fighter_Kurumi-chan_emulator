"""最小交易单位 / lot_size 校验。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.enums import ErrorCode, Market, TradingError
from app.core.symbols import HK_ALLOWED_LOT_SIZES, SYMBOLS, _hk


def test_all_builtin_hk_symbols_use_legal_lot_sizes() -> None:
    hk_specs = [s for s in SYMBOLS.values() if s.market is Market.HK]
    assert hk_specs, "至少要有港股标的"
    for spec in hk_specs:
        assert spec.lot_size in HK_ALLOWED_LOT_SIZES


def test_hk_rejects_illegal_lot_size_at_definition() -> None:
    with pytest.raises(ValueError):
        _hk("BAD.HK", "非法手数", "10", 300, "0.01")


@pytest.mark.parametrize("lot_size", sorted(HK_ALLOWED_LOT_SIZES))
def test_hk_accepts_every_legal_lot_size(lot_size: int) -> None:
    spec = _hk("TEST.HK", "测试", "10", lot_size, "0.01")
    spec.validate_qty(Decimal(lot_size))
    spec.validate_qty(Decimal(lot_size * 3))


def test_hk_qty_must_be_multiple_of_lot_size() -> None:
    spec = SYMBOLS["00700.HK"]  # 每手 100 股
    spec.validate_qty(Decimal("100"))
    spec.validate_qty(Decimal("300"))
    with pytest.raises(TradingError) as excinfo:
        spec.validate_qty(Decimal("150"))
    assert excinfo.value.code is ErrorCode.LOT_SIZE_VIOLATION


def test_hk_qty_below_one_lot_is_invalid() -> None:
    spec = SYMBOLS["00700.HK"]
    with pytest.raises(TradingError) as excinfo:
        spec.validate_qty(Decimal("50"))
    assert excinfo.value.code is ErrorCode.INVALID_QTY


def test_us_allows_single_share_and_rejects_fractional() -> None:
    spec = SYMBOLS["AAPL"]
    spec.validate_qty(Decimal("1"))
    spec.validate_qty(Decimal("3"))
    with pytest.raises(TradingError) as excinfo:
        spec.validate_qty(Decimal("1.5"))
    assert excinfo.value.code is ErrorCode.LOT_SIZE_VIOLATION


def test_fx_uses_lot_step_of_one_hundredth() -> None:
    spec = SYMBOLS["EURUSD"]
    spec.validate_qty(Decimal("0.01"))
    spec.validate_qty(Decimal("1.25"))
    # 低于最小手数
    with pytest.raises(TradingError) as excinfo:
        spec.validate_qty(Decimal("0.005"))
    assert excinfo.value.code is ErrorCode.INVALID_QTY
    # 不是 0.01 手的整数倍
    with pytest.raises(TradingError) as excinfo:
        spec.validate_qty(Decimal("0.015"))
    assert excinfo.value.code is ErrorCode.LOT_SIZE_VIOLATION


def test_non_positive_qty_rejected() -> None:
    for symbol in ("AAPL", "00700.HK", "EURUSD"):
        spec = SYMBOLS[symbol]
        with pytest.raises(TradingError) as excinfo:
            spec.validate_qty(Decimal("0"))
        assert excinfo.value.code is ErrorCode.INVALID_QTY
