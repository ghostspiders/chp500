"""东财 push2 数据源闭环（构建级）离线测试：monkeypatch 网络，锁死接线。

严格真实模式：东财 push2 不可达时构建直接报错终止，绝不回落 reference/synthetic/static。
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


def _stub_earnings(as_of=None, dates=None) -> pd.DataFrame:
    # 单只、单季（Q1）：TTM=最新单季=net_profit
    return pd.DataFrame({
        "code": ["600000"], "date": ["20260331"],
        "net_profit": [1e9], "industry": ["银行"],
    })


def test_build_a_listing_snapshot_raises_when_em_unreachable(monkeypatch):
    monkeypatch.setattr(adapters, "fetch_a_quotes_sina", _stub_quotes)
    monkeypatch.setattr(adapters, "fetch_a_earnings", _stub_earnings)
    monkeypatch.setattr(adapters, "fetch_hist", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(adapters, "get_em_spot", lambda market: None)

    # 东财 push2 不可达 → 直接报错终止，绝不回落参考/合成近似
    with pytest.raises(RuntimeError):
        adapters.build_a_listing_snapshot(pd.Timestamp("2026-08-15"))


def test_build_a_listing_snapshot_em_on_reachable(monkeypatch):
    # 构造覆盖全部 curated A 代码的东财快照，验证命中即标记 em
    ref = pd.read_csv(DATA_DIR / "demo_universe.csv", dtype={"code": str})
    codes = ref["code"].astype(str).tolist()
    fake = pd.DataFrame({
        "code": codes,
        "name": ["x"] * len(codes),
        "price": [10.0] * len(codes),
        "total_mcap_local": [1e11] * len(codes),
        "float_mcap_local": [5e10] * len(codes),
    })

    monkeypatch.setattr(adapters, "fetch_a_quotes_sina", _stub_quotes)
    monkeypatch.setattr(adapters, "fetch_a_earnings", _stub_earnings)
    monkeypatch.setattr(adapters, "fetch_hist", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(adapters, "get_em_spot", lambda market: fake if market == "A" else None)

    out = adapters.build_a_listing_snapshot(pd.Timestamp("2026-08-15"))
    assert (out["shares_source"] == "em").all()
    assert out["shares_source"].isin(["em", "missing"]).all()


def test_build_hk_us_listing_snapshot_raises_when_em_unreachable(monkeypatch):
    hk = pd.read_csv(DATA_DIR / "demo_hk.csv", dtype={"code": str})
    codes = hk["code"].astype(str).tolist()
    monkeypatch.setattr(adapters, "fetch_hk_us_price", lambda *a, **k: pd.Series(dtype=float))
    monkeypatch.setattr(adapters, "fetch_hk_us_hist", lambda *a, **k: (pd.DataFrame(), pd.DataFrame()))
    # 真实行业源可达（只取真实行业）；东财 push2 不可达 → 应报错终止
    monkeypatch.setattr(adapters, "fetch_hk_us_sector", lambda codes, market: {c: "金融" for c in codes})
    monkeypatch.setattr(adapters, "get_em_spot", lambda market: None)
    monkeypatch.setattr(adapters.fx, "fx_rate_on", lambda *a, **k: 1.0)

    # 东财 push2（HK）不可达 → 直接报错终止，绝不回落静态参考
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
    # 真实行业（无静态）：这里用桩返回真实行业字符串
    monkeypatch.setattr(adapters, "fetch_hk_us_sector", lambda codes, market: {c: "银行" for c in codes})
    monkeypatch.setattr(adapters, "get_em_spot", lambda market: pd.DataFrame({
        "code": codes, "name": ["x"] * len(codes), "price": [10.0] * len(codes),
        "total_mcap_local": [1e11] * len(codes), "float_mcap_local": [5e10] * len(codes),
    }))
    monkeypatch.setattr(adapters.fx, "fx_rate_on", lambda *a, **k: 1.0)
    # 真实上市日由 Sina 全量日线首日推导
    monkeypatch.setattr(adapters, "_cached_hk_us_daily", lambda code, market: pd.DataFrame({"date": pd.to_datetime(["2014-09-19"])}))

    def _fake_earn(out, market, usd_cny=7.1):
        out = out.copy()
        out["ttm_net_profit"] = 1e9
        out["latest_q_net_profit"] = 1e8
        out["profit_source"] = "em" if market == "HK" else "edgar"
        return out
    monkeypatch.setattr(adapters, "apply_real_earnings", _fake_earn)
    monkeypatch.setattr(adapters, "compute_liquidity", lambda volumes, float_shares, months=6: {c: 1.0 for c in volumes.columns})

    out = adapters.build_hk_us_listing_snapshot(pd.Timestamp("2026-08-15"), "HK", pd.DataFrame())
    assert not out.empty
    assert (out["sector"] == "金融").all()          # 真实行业「银行」→ GICS 金融
    assert (out["industry"] == "银行").all()         # 真实行业原值保留
    assert (out["listing_date"] == pd.Timestamp("2014-09-19")).all()  # 真实上市日推导
    assert (out["shares_source"] == "em").all()      # 真实东财 push2 覆盖

