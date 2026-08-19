"""腾讯行情快照数据源闭环（构建级）离线测试：monkeypatch 网络，锁死接线。

严格真实模式：腾讯快照/雪球行业不可达时构建直接报错终止，绝不回落 reference/synthetic/static。
与 tests/test_real_sources.py 的纯函数单测互补（函数级 + 构建级）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data import adapters
from chp500.data.adapters import DATA_DIR


def _stub_quotes() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["600000"], "name": ["x"], "price": [10.0],
        "volume": [1], "amount": [1],
    })


def test_build_a_listing_snapshot_raises_when_spot_unreachable(monkeypatch):
    monkeypatch.setattr(adapters, "fetch_a_quotes_sina", _stub_quotes)
    monkeypatch.setattr(adapters, "fetch_hist", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(adapters, "fetch_a_industry", lambda codes: {c: "银行" for c in codes})
    monkeypatch.setattr(adapters, "fetch_spot", lambda market, codes: None)

    # 腾讯快照不可达 -> 直接报错终止，绝不回落参考/合成近似
    with pytest.raises(RuntimeError):
        adapters.build_a_listing_snapshot(pd.Timestamp("2026-08-15"))


def test_build_a_listing_snapshot_raises_when_industry_unreachable(monkeypatch):
    monkeypatch.setattr(adapters, "fetch_a_quotes_sina", _stub_quotes)
    monkeypatch.setattr(adapters, "fetch_hist", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(adapters, "fetch_a_industry", lambda codes: {})  # 雪球行业源不可用
    monkeypatch.setattr(adapters, "fetch_spot", lambda market, codes: None)

    with pytest.raises(RuntimeError):
        adapters.build_a_listing_snapshot(pd.Timestamp("2026-08-15"))


def test_build_a_listing_snapshot_tencent_on_reachable(monkeypatch):
    # 构造覆盖全部 curated A 代码的腾讯快照，验证命中即标记 tencent 且 PE 推导净利
    ref = pd.read_csv(DATA_DIR / "demo_universe.csv", dtype={"code": str})
    codes = ref["code"].astype(str).tolist()
    fake = pd.DataFrame({
        "code": codes,
        "name": ["x"] * len(codes),
        "price": [10.0] * len(codes),
        "total_mcap_local": [1e11] * len(codes),
        "float_mcap_local": [5e10] * len(codes),
        "pe_ttm": [10.0] * len(codes),
    })

    monkeypatch.setattr(adapters, "fetch_a_quotes_sina", _stub_quotes)
    monkeypatch.setattr(adapters, "fetch_hist", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(adapters, "fetch_a_industry", lambda codes: {c: "银行" for c in codes})
    monkeypatch.setattr(adapters, "fetch_spot", lambda market, codes: fake if market == "A" else None)

    out = adapters.build_a_listing_snapshot(pd.Timestamp("2026-08-15"))
    assert (out["shares_source"] == "tencent").all()
    assert (out["profit_source"] == "tencent").all()
    assert (out["ttm_net_profit"] == 1e11 / 10.0).all()  # 总市值/PE(TTM)
    assert (out["industry"] == "银行").all()  # 雪球行业


def test_build_hk_us_listing_snapshot_raises_when_spot_unreachable(monkeypatch):
    monkeypatch.setattr(adapters, "fetch_hk_us_price", lambda *a, **k: pd.Series(dtype=float))
    monkeypatch.setattr(adapters, "fetch_hk_us_hist", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(adapters, "fetch_spot", lambda market, codes: None)
    monkeypatch.setattr(adapters.fx, "fx_rate_on", lambda *a, **k: 1.0)

    # 腾讯快照（HK）不可达 -> 直接报错终止，绝不回落静态参考
    with pytest.raises(RuntimeError):
        adapters.build_hk_us_listing_snapshot(pd.Timestamp("2026-08-15"), "HK", pd.DataFrame())


def test_first_listed_date_from_real_history(monkeypatch):
    monkeypatch.setattr(adapters, "_cached_hk_us_daily", lambda code, market: pd.DataFrame(
        {"date": pd.to_datetime(["2010-01-04", "2010-01-05"])}
    ))
    assert adapters._first_listed_date("00700", "HK") == pd.Timestamp("2010-01-04")


def test_first_listed_date_empty(monkeypatch):
    monkeypatch.setattr(adapters, "_cached_hk_us_daily", lambda code, market: pd.DataFrame())
    assert pd.isna(adapters._first_listed_date("00700", "HK"))


def test_build_hk_us_listing_snapshot_real_sector_and_listing(monkeypatch):
    hk = pd.read_csv(DATA_DIR / "demo_hk.csv", dtype={"code": str})
    codes = hk["code"].astype(str).tolist()

    monkeypatch.setattr(adapters, "fetch_hk_us_price", lambda codes, market, as_of: pd.Series({c: 10.0 for c in codes}))
    monkeypatch.setattr(adapters, "fetch_hk_us_hist", lambda codes, market, s, e, **k: (pd.DataFrame(), pd.DataFrame({c: [1] for c in codes})))
    monkeypatch.setattr(adapters, "fetch_spot", lambda market, codes: pd.DataFrame({
        "code": codes, "name": ["x"] * len(codes), "price": [10.0] * len(codes),
        "total_mcap_local": [1e11] * len(codes), "float_mcap_local": [5e10] * len(codes),
        "pe_ttm": [10.0] * len(codes),
    }))
    monkeypatch.setattr(adapters.fx, "fx_rate_on", lambda *a, **k: 1.0)
    # 真实上市日由 Sina 全量日线首日推导
    monkeypatch.setattr(adapters, "_cached_hk_us_daily", lambda code, market: pd.DataFrame({"date": pd.to_datetime(["2014-09-19"])}))
    monkeypatch.setattr(adapters, "compute_liquidity", lambda volumes, float_shares, months=6: {c: 1.0 for c in volumes.columns})

    out = adapters.build_hk_us_listing_snapshot(pd.Timestamp("2026-08-15"), "HK", pd.DataFrame())
    assert not out.empty
    # 行业取自参考表人工核定列（与 code 对齐），板块映射非空
    assert (out["industry"].notna()).all()
    ref_ind = hk.set_index("code").loc[out["code"], "industry"]
    assert out["industry"].tolist() == ref_ind.tolist()
    assert (out["sector"].notna()).all()
    assert "金融" in out["sector"].tolist()  # 银行 -> GICS 金融
    assert (out["listing_date"] == pd.Timestamp("2014-09-19")).all()  # 真实上市日推导
    assert (out["shares_source"] == "tencent").all()  # 腾讯真实快照覆盖
    assert (out["profit_source"] == "tencent").all()  # HK 净利由 PE 推导
    assert (out["ttm_net_profit"] == 1e11 / 10.0).all()
