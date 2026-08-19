"""数据适配层纯函数（流动性）单元测试（不触网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data.adapters import compute_liquidity


def test_compute_liquidity_ratio():
    # 10 个交易日、每日成交量 1e6；流通股 1e8 -> 比率 = 1e7/1e8 = 0.1
    vol = pd.DataFrame({"600000": [1e6] * 10})
    float_shares = pd.Series({"600000": 1e8})
    ratio = compute_liquidity(vol, float_shares, months=6)
    assert ratio["600000"] == pytest.approx(0.1)


def test_compute_liquidity_window_tail():
    # 窗口取末尾 months*21 = 126 个交易日；前面的大成交量应被排除
    n = 250
    vol = pd.DataFrame({"600000": [1e6] * n})
    vol.iloc[: n - 126, 0] = 1e9
    ratio = compute_liquidity(vol, pd.Series({"600000": 1e8}), months=6)
    assert ratio["600000"] == pytest.approx((126 * 1e6) / 1e8)
