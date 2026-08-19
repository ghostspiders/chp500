"""真实股本/财务接入（adapters 严格真实模式）单元测试。

严格模式：腾讯快照（市值/股本/PE 推导净利）/ SEC EDGAR 不可达时直接报错，
绝不回落 reference/synthetic/static。
"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data import adapters


def _a_listing() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["600000", "603444"],
        "market": "A",
        "price": [10.0, 389.8],
        "total_shares": [1e10, 3.568e9],
        "float_shares": [5e9, 2.38e9],
        "iwf": [0.5, 0.667],
        "total_mcap": [1e11, 1.39e12],
        "float_mcap": [5e10, 9.28e11],
    })


def _spot_a() -> pd.DataFrame:
    return pd.DataFrame([
        # 吉比特真实：0.72 亿股，总市值约 280 亿，全流通；PE(TTM)=14 -> TTM 净利 20.04 亿
        {"code": "603444", "name": "吉比特", "price": 389.8,
         "total_mcap_local": 2.806e10, "float_mcap_local": 2.806e10, "pe_ttm": 14.0},
    ])


def test_apply_spot_shares_tencent_or_missing():
    # 命中腾讯快照者标记 tencent 并推导 TTM 净利=总市值/PE；未命中标记 missing 置 NaN
    out = adapters.apply_spot_shares(_a_listing(), _spot_a(), fxr=1.0)
    g = out[out["code"] == "603444"].iloc[0]
    assert g["shares_source"] == "tencent"
    assert g["total_shares"] == 2.806e10 / 389.8
    assert g["iwf"] == 1.0
    assert g["total_mcap"] == 2.806e10
    assert g["ttm_net_profit"] == pytest.approx(2.806e10 / 14.0)
    assert g["profit_source"] == "tencent"
    assert pd.isna(g["latest_q_net_profit"])  # 单季净利随东财业绩源移除
    other = out[out["code"] == "600000"].iloc[0]
    assert other["shares_source"] == "missing"
    assert other["profit_source"] == "missing"
    assert pd.isna(other["total_shares"])
    assert pd.isna(other["ttm_net_profit"])


def test_apply_spot_shares_negative_pe_means_loss():
    # 亏损股 PE 为负 -> TTM 净利推导为负（盈利筛选自然剔除，不误杀信息）
    spot = pd.DataFrame([
        {"code": "600000", "name": "亏损股", "price": 10.0,
         "total_mcap_local": 1e10, "float_mcap_local": 5e9, "pe_ttm": -50.0},
    ])
    out = adapters.apply_spot_shares(_a_listing(), spot, fxr=1.0)
    r = out[out["code"] == "600000"].iloc[0]
    assert r["ttm_net_profit"] == pytest.approx(1e10 / -50.0)
    assert r["profit_source"] == "tencent"


def test_apply_spot_shares_missing_pe_nan():
    # PE 缺失（快照无 pe_ttm 列）-> 净利 NaN（下游盈利筛选剔除），市值/股本照常
    spot = _spot_a().drop(columns=["pe_ttm"])
    out = adapters.apply_spot_shares(_a_listing(), spot, fxr=1.0)
    r = out[out["code"] == "603444"].iloc[0]
    assert pd.isna(r["ttm_net_profit"])
    assert r["total_mcap"] == 2.806e10


def test_apply_spot_shares_none_raises():
    with pytest.raises(RuntimeError):
        adapters.apply_spot_shares(_a_listing(), None, fxr=1.0)


def _hk_listing() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["00700", "030760"],  # 030760 为 demo 表中的非标代码（快照源无此码）
        "market": ["HK", "HK"],
        "price": [400.0, 290.0],
        "total_shares": [9.6e9, 1.2e9],
        "float_shares": [9.1e9, 1.1e9],
        "iwf": [0.95, 0.9],
        "total_mcap_local": [3.84e12, 3.48e11],
        "float_mcap_local": [3.64e12, 3.19e11],
        "total_mcap": [3.84e12 * 0.91, 3.48e11 * 0.91],
        "float_mcap": [3.64e12 * 0.91, 3.19e11 * 0.91],
    })


def _spot_hk() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "00700", "name": "腾讯控股", "price": 446.4,
         "total_mcap_local": 4.0636e12, "float_mcap_local": 4.0636e12, "pe_ttm": 16.18},
    ])


def test_apply_spot_shares_hk_with_fx():
    # HK：市值折 CNY（fxr=0.91），TTM 净利 = 总市值(CNY)/PE
    out = adapters.apply_spot_shares(_hk_listing(), _spot_hk(), fxr=0.91)
    t = out[out["code"] == "00700"].iloc[0]
    assert t["shares_source"] == "tencent"
    assert t["price"] == 446.4
    assert t["total_shares"] == 4.0636e12 / 446.4
    assert t["total_mcap"] == 4.0636e12 * 0.91  # CNY
    assert t["ttm_net_profit"] == pytest.approx(4.0636e12 * 0.91 / 16.18)
    m = out[out["code"] == "030760"].iloc[0]
    assert m["shares_source"] == "missing"
    assert pd.isna(m["total_shares"])


def test_apply_spot_shares_us_rows_left_for_edgar():
    # 美股不在快照层推导净利（由 SEC EDGAR 权威覆盖），保持 missing 待 EDGAR 覆盖
    listing = pd.DataFrame({
        "code": ["BABA", "NIO"],
        "market": ["US", "US"],
        "price": [100.0, 5.0],
    })
    spot = pd.DataFrame([
        {"code": "BABA", "name": "阿里巴巴", "price": 100.0,
         "total_mcap_local": 2.0e11, "float_mcap_local": 1.9e11, "pe_ttm": 19.56},
        {"code": "NIO", "name": "蔚来", "price": 5.0,
         "total_mcap_local": 1.0e10, "float_mcap_local": 0.9e10, "pe_ttm": -8.0},
    ])
    out = adapters.apply_spot_shares(listing, spot, fxr=7.1)
    assert (out["profit_source"] == "missing").all()
    assert out["ttm_net_profit"].isna().all()
    assert (out["shares_source"] == "tencent").all()  # 股本/市值照常覆盖
    assert out["total_mcap"].iloc[0] == pytest.approx(2.0e11 * 7.1)


def test_apply_spot_shares_local_none_raises(monkeypatch):
    with pytest.raises(RuntimeError):
        adapters.apply_spot_shares(_hk_listing(), None, fxr=0.91)


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
    assert b["latest_q_net_profit"] == 3.0e10  # EDGAR 仍有单季口径
    # NIO 无真实财务：标记 missing，净利润置 NaN（不回落 static）
    n = out[out["code"] == "NIO"].iloc[0]
    assert n["profit_source"] == "missing" and pd.isna(n["ttm_net_profit"])


def test_apply_real_earnings_hk_is_noop(monkeypatch):
    # HK 净利已由腾讯 PE 推导（apply_spot_shares），本函数不再处理港股
    df = pd.DataFrame({
        "code": ["00700"],
        "ttm_net_profit": [100.0],
        "profit_source": ["tencent"],
    })
    out = adapters.apply_real_earnings(df, "HK", usd_cny=7.1)
    assert out["ttm_net_profit"].iloc[0] == 100.0
    assert out["profit_source"].iloc[0] == "tencent"


def test_apply_real_earnings_exception_raises(monkeypatch):
    def _boom(ticker, usd_cny=7.1):
        raise RuntimeError("network down")

    monkeypatch.setattr(adapters.edgar, "fetch_us_net_income", _boom)
    # 整批均无真实财务（源整体不可用）-> 报错终止，不静默回落 static
    with pytest.raises(RuntimeError):
        adapters.apply_real_earnings(_us_listing(), "US", usd_cny=7.1)


def test_attach_industry_fills_missing_a_rows(monkeypatch):
    # 扩展宇宙 A 股选样后补行业：仅补 market=A 且 industry 为空的行
    monkeypatch.setattr(adapters, "fetch_a_industry",
                        lambda codes: {c: "白酒" for c in codes})
    df = pd.DataFrame({
        "code": ["600519", "000001", "00700"],
        "market": ["A", "A", "HK"],
        "industry": [None, "银行", "软件服务"],
    })
    out = adapters.attach_industry(df)
    assert out["industry"].tolist() == ["白酒", "银行", "软件服务"]
