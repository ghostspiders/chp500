"""筛选（screens）单元测试：6 大准入指标边界与成分选取。"""

from __future__ import annotations

import pandas as pd

from chp500.filter.screens import (
    add_screen_diagnostics,
    check_deletion,
    select_constituents,
    select_eligible,
)

from conftest import AS_OF, SCREEN_CFG, make_snapshot


def _one(overrides: dict) -> pd.Series:
    diag = add_screen_diagnostics(make_snapshot([overrides]), AS_OF, SCREEN_CFG)
    return diag.iloc[0]


def test_default_row_passes_all_screens():
    r = _one({})
    assert r["eligible"]
    assert r["fail_reasons"] == ""


def test_st_fails():
    r = _one({"is_st": True})
    assert not r["pass_st"] and not r["eligible"]
    assert "ST/可投资性" in r["fail_reasons"]


def test_listing_age_boundary():
    # 上市 11 个月 -> 不通过；13 个月 -> 通过
    r11 = _one({"listing_date": (AS_OF - pd.Timedelta(days=330)).strftime("%Y-%m-%d")})
    r13 = _one({"listing_date": (AS_OF - pd.Timedelta(days=396)).strftime("%Y-%m-%d")})
    assert not r11["pass_listing"] and "上市不足" in r11["fail_reasons"]
    assert r13["pass_listing"]


def test_mcap_boundary():
    assert not _one({"total_mcap": SCREEN_CFG["mcap_min"] - 1})["pass_mcap"]
    assert _one({"total_mcap": SCREEN_CFG["mcap_min"]})["pass_mcap"]


def test_iwf_boundary():
    assert not _one({"iwf": 0.199})["pass_iwf"]
    assert _one({"iwf": 0.20})["pass_iwf"]


def test_profit_requires_ttm_positive():
    # 盈利门槛仅看 TTM>0（东财业绩源移除后无单季数据；PE 缺失 -> NaN 亦不通过）
    assert not _one({"ttm_net_profit": -1.0})["pass_profit"]
    assert not _one({"ttm_net_profit": float("nan")})["pass_profit"]
    assert _one({"ttm_net_profit": 1e9})["pass_profit"]


def test_liquidity_per_market_threshold():
    # A 股 0.02；HK/US 0.30；未知市场回退全局 1.0
    assert _one({"market": "A", "liquidity_ratio": 0.02})["pass_liquidity"]
    assert not _one({"market": "A", "liquidity_ratio": 0.019})["pass_liquidity"]
    assert _one({"market": "HK", "liquidity_ratio": 0.30})["pass_liquidity"]
    assert not _one({"market": "US", "liquidity_ratio": 0.29})["pass_liquidity"]
    assert _one({"market": "XX", "liquidity_ratio": 1.0})["pass_liquidity"]
    assert not _one({"market": "XX", "liquidity_ratio": 0.99})["pass_liquidity"]


def test_non_china_fails():
    r = _one({"is_china": False})
    assert not r["pass_china"] and "非中国公司" in r["fail_reasons"]


def test_select_constituents_takes_top_n_by_float_mcap():
    df = make_snapshot([
        {"float_mcap": 1.0e12},
        {"float_mcap": 3.0e12},
        {"float_mcap": 2.0e12},
        {"float_mcap": 9.0e12, "is_st": True},  # 最大但 ST，被筛除
    ])
    cfg = dict(SCREEN_CFG, target_count=2)
    out = select_constituents(df, AS_OF, cfg)
    assert len(out) == 2
    assert list(out.sort_values("float_mcap", ascending=False)["float_mcap"]) == [3.0e12, 2.0e12]


def test_select_eligible_drops_failures():
    df = make_snapshot([{}, {"is_st": True}])
    out = select_eligible(df, AS_OF, SCREEN_CFG)
    assert len(out) == 1


def test_check_deletion_reasons_and_priority():
    df = make_snapshot([
        {},  # 全项正常
        {"is_st": True, "ttm_net_profit": -1.0},  # ST 优先于财务恶化
        {"ttm_net_profit": -1.0},
        {"total_mcap": 3.0e10},
        {"iwf": 0.40},
        {"is_china": False},
    ])
    out = check_deletion(df, AS_OF, SCREEN_CFG).set_index("entity_id")
    reasons = out["delist_reason"].to_dict()
    assert reasons["A.600000"] == "" and not out.loc["A.600000", "delist"]
    assert reasons["A.600001"] == "ST/退市"
    assert reasons["A.600002"] == "财务恶化"
    assert reasons["A.600003"] == "市值缩水"
    assert reasons["A.600004"] == "IWF跌破"
    assert reasons["A.600005"] == "非中国"
    assert out["delist"].sum() == 5
