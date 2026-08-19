"""scripts/build_index._clean_price 单元测试：只清洗单日回弹尖刺，保留真实持续行情。"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_index import _clean_price


def test_spike_replaced_with_prev():
    # 单日尖刺：+50% 后次日回到跳变前水平（±10% 内）-> 用前值替代
    ser = pd.Series([10.0, 10.2, 15.3, 10.1, 10.3])
    out = _clean_price(ser)
    assert out.iloc[2] == pytest.approx(10.2)
    assert out.iloc[3] == pytest.approx(10.1)


def test_sustained_crash_kept():
    # 持续暴跌（次日未回到原水平）必须保留，不得抹平（回归：旧逻辑一律替换）
    ser = pd.Series([10.0, 10.2, 5.0, 4.9, 5.1])
    out = _clean_price(ser)
    assert out.iloc[2] == pytest.approx(5.0)


def test_sustained_rally_kept():
    ser = pd.Series([10.0, 10.2, 16.0, 16.5, 17.0])
    out = _clean_price(ser)
    assert out.iloc[2] == pytest.approx(16.0)


def test_zero_cleaned():
    ser = pd.Series([10.0, 0.0, 10.2])
    out = _clean_price(ser)
    assert (out > 0).all()


def test_normal_series_untouched():
    ser = pd.Series([10.0, 10.5, 11.0, 10.8, 11.2])
    out = _clean_price(ser)
    pd.testing.assert_series_equal(out, ser)
