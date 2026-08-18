"""数据适配层纯函数（TTM / 流动性）单元测试（不触网）。"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data.adapters import compute_liquidity, compute_ttm_earnings


def _earnings(cum: dict[str, float], industry: str = "银行") -> pd.DataFrame:
    return pd.DataFrame(
        [{"code": "600000", "date": d, "net_profit": v, "industry": industry} for d, v in cum.items()]
    )


def test_ttm_formula_standard_quarters():
    # 累计净利：24Q1=10, 24Q2=25, 24Q3=40, 24FY=60, 25Q1=15, 25Q2=35
    df = _earnings({"20240331": 10, "20240630": 25, "20240930": 40, "20241231": 60,
                    "20250331": 15, "20250630": 35})
    out = compute_ttm_earnings(df)
    r = out[out["code"] == "600000"].iloc[0]
    # TTM = C(25Q2) - C(24Q2) + FY(24) = 35 - 25 + 60 = 70
    assert r["ttm_net_profit"] == pytest.approx(70)
    # 最新单季 = 35 - 15 = 20
    assert r["latest_q_net_profit"] == pytest.approx(20)
    assert r["industry"] == "银行"


def test_ttm_latest_q1_is_cumulative_directly():
    df = _earnings({"20241231": 60, "20250331": 15})
    out = compute_ttm_earnings(df)
    r = out.iloc[0]
    assert r["latest_q_net_profit"] == pytest.approx(15)
    # TTM 缺 2024Q1 -> NaN
    assert pd.isna(r["ttm_net_profit"])


def test_ttm_q1_full_history():
    df = _earnings({"20240331": 10, "20241231": 60, "20250331": 15})
    r = compute_ttm_earnings(df).iloc[0]
    # TTM = 15 - 10 + 60 = 65；最新单季 = 15（Q1 直接取累计）
    assert r["ttm_net_profit"] == pytest.approx(65)
    assert r["latest_q_net_profit"] == pytest.approx(15)


def test_ttm_deduplicates_report_rows():
    df = pd.concat([
        _earnings({"20241231": 60, "20250331": 15, "20240331": 10}),
        _earnings({"20250331": 999}),  # 重复报告期，应保留首条
    ], ignore_index=True)
    r = compute_ttm_earnings(df).iloc[0]
    assert r["ttm_net_profit"] == pytest.approx(65)


def test_ttm_empty_input():
    out = compute_ttm_earnings(pd.DataFrame(columns=["code", "date", "net_profit", "industry"]))
    assert out.empty


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
