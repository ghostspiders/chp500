"""真实股本/财务接入（universe / adapters 严格真实模式）单元测试。

严格模式：东财 push2 / 真实财务不可达时直接报错，绝不回落 reference/synthetic/static。
"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data import adapters
from chp500.data.universe import apply_em_shares


def _a_listing() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["600000", "603444"],
        "price": [10.0, 389.8],
        "total_shares": [1e10, 3.568e9],
        "float_shares": [5e9, 2.38e9],
        "iwf": [0.5, 0.667],
        "total_mcap": [1e11, 1.39e12],
        "float_mcap": [5e10, 9.28e11],
    })


def _em_a() -> pd.DataFrame:
    return pd.DataFrame([
        # 吉比特真实：0.72 亿股，总市值约 280 亿，流通=总（全流通）
        {"code": "603444", "name": "吉比特", "price": 389.8,
         "total_mcap_local": 2.806e10, "float_mcap_local": 2.806e10},
    ])


def test_apply_em_shares_em_or_missing():
    # 命中东财 push2 者标记 em；未命中标记 missing 且市值/股本置 NaN（下游剔除，不回填近似）
    out = apply_em_shares(_a_listing(), _em_a())
    g = out[out["code"] == "603444"].iloc[0]
    assert g["shares_source"] == "em"
    assert g["total_shares"] == 2.806e10 / 389.8
    assert g["iwf"] == 1.0
    assert g["total_mcap"] == 2.806e10
    other = out[out["code"] == "600000"].iloc[0]
    assert other["shares_source"] == "missing"
    assert pd.isna(other["total_shares"])
    assert pd.isna(other["total_mcap"])


def test_apply_em_shares_none_raises():
    with pytest.raises(RuntimeError):
        apply_em_shares(_a_listing(), None)


def _hk_listing() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["00700", "030760"],  # 030760 为 demo 表中的非标代码（EM 无此码）
        "price": [400.0, 290.0],
        "total_shares": [9.6e9, 1.2e9],
        "float_shares": [9.1e9, 1.1e9],
        "iwf": [0.95, 0.9],
        "total_mcap_local": [3.84e12, 3.48e11],
        "float_mcap_local": [3.64e12, 3.19e11],
        "total_mcap": [3.84e12 * 0.91, 3.48e11 * 0.91],
        "float_mcap": [3.64e12 * 0.91, 3.19e11 * 0.91],
    })


def _em_hk() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "00700", "name": "腾讯控股", "price": 446.4,
         "total_mcap_local": 4.0636e12, "float_mcap_local": 4.0636e12},
    ])


def test_apply_em_shares_local_hk(monkeypatch):
    out = adapters.apply_em_shares_local(_hk_listing(), _em_hk(), fxr=0.91)
    t = out[out["code"] == "00700"].iloc[0]
    assert t["shares_source"] == "em"
    assert t["price"] == 446.4
    assert t["total_shares"] == 4.0636e12 / 446.4
    assert t["total_mcap"] == 4.0636e12 * 0.91  # CNY
    m = out[out["code"] == "030760"].iloc[0]
    assert m["shares_source"] == "missing"
    assert pd.isna(m["total_shares"])


def test_apply_em_shares_local_none_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        adapters.apply_em_shares_local(_hk_listing(), None, fxr=0.91)


def _a_reference_listing() -> pd.DataFrame:
    # 精选(curated) A 股基线（不再作为回落近似，仅用于验证命中/未命中分流）
    return pd.DataFrame({
        "code": ["600000", "603444"],
        "price": [10.0, 389.8],
        "total_shares": [1e10, 3.568e9],
        "float_shares": [5e9, 2.38e9],
        "iwf": [0.5, 0.667],
        "total_mcap_local": [1e11, 1.39e12],
        "float_mcap_local": [5e10, 9.28e11],
        "total_mcap": [1e11, 1.39e12],
        "float_mcap": [5e10, 9.28e11],
    })


def test_apply_em_shares_local_em_or_missing(monkeypatch):
    # 命中东财 push2 者标记 em，未命中标记 missing（不再回落人工核定参考表）
    out = adapters.apply_em_shares_local(_a_reference_listing(), _em_a(), fxr=1.0)
    hit = out[out["code"] == "603444"].iloc[0]
    assert hit["shares_source"] == "em"
    assert hit["total_shares"] == 2.806e10 / 389.8
    assert hit["iwf"] == 1.0
    miss = out[out["code"] == "600000"].iloc[0]
    assert miss["shares_source"] == "missing"
    assert pd.isna(miss["total_shares"])


def _us_listing() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["BABA", "NIO"],
        "ttm_net_profit": [1.0e11, -1.0e10],
        "latest_q_net_profit": [1.0e11, -1.0e10],
    })


def test_apply_real_earnings_us_edgar(monkeypatch):
    fake = {
        "BABA": {"ttm": 1.2e11, "latest_q": 3.0e10, "granularity": "half", "latest_end": None, "currency": "CNY"},
    }

    def _fake_fetch(ticker, usd_cny=7.1):
        return fake.get(ticker)

    monkeypatch.setattr(adapters.edgar, "fetch_us_net_income", _fake_fetch)
    out = adapters.apply_real_earnings(_us_listing(), "US", usd_cny=7.1)
    b = out[out["code"] == "BABA"].iloc[0]
    assert b["profit_source"] == "edgar" and b["ttm_net_profit"] == 1.2e11
    # NIO 无真实财务：标记 missing，净利润置 NaN（不回落 static）
    n = out[out["code"] == "NIO"].iloc[0]
    assert n["profit_source"] == "missing" and pd.isna(n["ttm_net_profit"])


def test_apply_real_earnings_hk_em(monkeypatch):
    periods = [
        (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31"), 2248.42e8),
        (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-06-30"), 1088.0e8),
        (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-30"), 1141.15e8),
    ]
    monkeypatch.setattr(adapters, "fetch_hk_profit_periods", lambda code: periods if code == "00700" else None)
    df = pd.DataFrame({
        "code": ["00700", "09988"],
        "ttm_net_profit": [1.0e11, 8.0e10],
        "latest_q_net_profit": [1.0e11, 8.0e10],
    })
    out = adapters.apply_real_earnings(df, "HK", usd_cny=7.1)
    t = out[out["code"] == "00700"].iloc[0]
    assert t["profit_source"] == "em"
    assert t["ttm_net_profit"] == 1141.15e8 - 1088.0e8 + 2248.42e8
    # 09988 无真实财报：标记 missing，不回落 static
    assert out[out["code"] == "09988"].iloc[0]["profit_source"] == "missing"


def test_apply_real_earnings_exception_raises(monkeypatch):
    def _boom(ticker, usd_cny=7.1):
        raise RuntimeError("network down")

    monkeypatch.setattr(adapters.edgar, "fetch_us_net_income", _boom)
    # 整批均无真实财务（源整体不可用）→ 报错终止，不静默回落 static
    with pytest.raises(RuntimeError):
        adapters.apply_real_earnings(_us_listing(), "US", usd_cny=7.1)
