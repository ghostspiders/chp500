"""跨市场实体合并（data.merge）单元测试。"""

from __future__ import annotations

import pandas as pd

from chp500.data.merge import merge_entities


def _listing(rows: list[dict]) -> pd.DataFrame:
    base_cols = {
        "entity_id": "X", "code": "00000", "name": "测试", "market": "A", "curr": "CNY",
        "is_st": False, "price": 10.0, "total_shares": 1e10, "iwf": 0.5,
        "float_shares": 5e9, "total_mcap": 1e11, "float_mcap": 5e10,
        "ttm_net_profit": 1e10, "latest_q_net_profit": 1e9, "sector": "金融",
        "industry": "银行", "liquidity_ratio": 0.5, "listing_date": "2015-06-30",
    }
    return pd.DataFrame([{**base_cols, **r} for r in rows])


def test_primary_listing_priority_a_over_hk_us():
    df = _listing([
        {"market": "US", "code": "XYZ", "curr": "USD", "float_mcap": 9.9e12},
        {"market": "HK", "code": "01234", "curr": "HKD", "float_mcap": 8.8e12},
        {"market": "A", "code": "600001", "curr": "CNY", "float_mcap": 7.7e12},
    ])
    out = merge_entities(df)
    assert len(out) == 1
    r = out.iloc[0]
    assert r["code"] == "600001" and r["market"] == "A" and r["float_mcap"] == 7.7e12
    assert r["n_listings"] == 3
    # 上市地清单按 A > HK > US 排序
    assert r["listings"] == "A:600001;HK:01234;US:XYZ"


def test_hk_primary_when_no_a_listing():
    df = _listing([
        {"market": "US", "code": "BABA", "float_mcap": 9.9e12},
        {"market": "HK", "code": "09988", "float_mcap": 8.8e12},
    ])
    out = merge_entities(df)
    assert out.iloc[0]["market"] == "HK" and out.iloc[0]["code"] == "09988"


def test_listing_date_takes_earliest_and_st_is_contagious():
    df = _listing([
        {"market": "A", "code": "600002", "listing_date": "2018-01-01"},
        {"market": "HK", "code": "01234", "listing_date": "2012-07-01", "is_st": True},
    ])
    out = merge_entities(df)
    r = out.iloc[0]
    assert pd.Timestamp(r["listing_date"]) == pd.Timestamp("2012-07-01")
    assert bool(r["is_st"])
    assert bool(r["is_china"])


def test_distinct_entities_kept_separately():
    df = _listing([
        {"entity_id": "E1", "market": "A", "code": "600001"},
        {"entity_id": "E2", "market": "HK", "code": "09999"},
    ])
    out = merge_entities(df)
    assert len(out) == 2
    assert set(out["entity_id"]) == {"E1", "E2"}


def test_empty_input_returns_empty():
    assert merge_entities(pd.DataFrame()).empty
