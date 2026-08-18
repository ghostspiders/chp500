"""指数序列（index.series）单元测试：除数、价格指数、全收益。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chp500.index.series import (
    build_series,
    initial_divisor,
    price_index,
    rebase_divisor,
    total_return_index,
)


def _mk_prices(data: dict) -> pd.DataFrame:
    return pd.DataFrame(data)


def test_initial_divisor():
    assert initial_divisor(1.0e12, base_point=1000.0) == pytest.approx(1.0e9)


def test_price_index_basic_math():
    dates = pd.date_range("2026-01-01", periods=3)
    prices = _mk_prices({
        "E1": [10.0, 11.0, 12.0],
        "E2": [100.0, 100.0, 100.0],
    })
    prices.index = dates
    shares = pd.Series({"E1": 1.0e9, "E2": 1.0e8})
    divisor = initial_divisor(2.0e10)  # 基期市值 200亿 / 基点 1000
    pi = price_index(prices, shares, divisor)
    assert pi.iloc[0] == pytest.approx(1000.0)
    # E1 涨 10%（+10亿） -> 总市值 210亿 -> 指数 1050
    assert pi.iloc[1] == pytest.approx(1050.0)
    assert pi.iloc[2] == pytest.approx(1100.0)


def test_build_series_base_point_and_columns():
    prices = _mk_prices({"E1": [10.0, 10.5, 11.0]})
    prices.index = pd.date_range("2026-01-01", periods=3)
    shares = pd.Series({"E1": 1.0e9})
    out = build_series(prices, shares)
    assert list(out.columns) == ["price_index", "total_return"]
    assert out["price_index"].iloc[0] == pytest.approx(1000.0)
    # 无分红时全收益 == 价格指数
    assert np.allclose(out["total_return"], out["price_index"])


def test_total_return_with_dividends_exceeds_price_index():
    # 单股票：价格不变，第 2 日除息 10%（每股分红 = 价格的 10%）
    prices = _mk_prices({"E1": [100.0, 100.0, 100.0, 100.0]})
    prices.index = pd.date_range("2026-01-01", periods=4)
    dividends = _mk_prices({"E1": [0.0, 10.0, 0.0, 0.0]})
    dividends.index = prices.index
    shares = pd.Series({"E1": 1.0e9})
    divisor = initial_divisor(1.0e11)

    pi = price_index(prices, shares, divisor)
    tri = total_return_index(prices, shares, divisor, dividends)
    assert pi.iloc[-1] == pytest.approx(1000.0)  # 价格指数不受分红影响
    assert tri.iloc[1] == pytest.approx(1000.0)  # 除息当日尚未再投资
    assert tri.iloc[2] == pytest.approx(1100.0)  # 次日起再投资生效
    assert tri.iloc[-1] > pi.iloc[-1]


def test_rebase_divisor_keeps_continuity():
    # 指数 1000 点、新总市值 2e12 -> 新除数应使点位维持 1000
    d = rebase_divisor(1000.0, 2.0e12)
    assert d == pytest.approx(2.0e9)
    assert (2.0e12 / d) == pytest.approx(1000.0)


def test_rebase_divisor_zero_level_fallback():
    assert rebase_divisor(0.0, 2.0e12) == pytest.approx(2.0e9)
