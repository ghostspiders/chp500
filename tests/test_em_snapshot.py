"""东财快照（em_snapshot）单元测试：分页组装、无效行过滤、失败降级。"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data import em_snapshot


def _page(total: int, diff: list) -> dict:
    return {"rc": 0, "data": {"total": total, "diff": diff}}


def _row(code: str, price=10.0, tm=1e10, fm=5e9):
    return {"f12": code, "f14": f"股票{code}", "f2": price, "f20": tm, "f21": fm}


def test_fetch_em_spot_multi_page(monkeypatch):
    pages = {
        1: _page(3, [_row("600001"), _row("600002")]),
        2: _page(3, [_row("600003")]),
    }
    monkeypatch.setattr(em_snapshot, "_fetch_page", lambda host, fs, page, page_size=1000: pages[page])
    df = em_snapshot.fetch_em_spot("A")
    assert list(df["code"]) == ["600001", "600002", "600003"]
    assert set(df.columns) == set(em_snapshot._COLUMNS)
    assert df["total_mcap_local"].iloc[0] == 1e10


def test_fetch_em_spot_filters_invalid_rows(monkeypatch):
    page = _page(3, [
        _row("600001"),
        {"f12": "600002", "f14": "停牌", "f2": "-", "f20": 1e10, "f21": 5e9},  # 停牌价 '-'
        {"f12": "RNWWW", "f14": "权证", "f2": 0.007, "f20": "-", "f21": "-"},  # 权证无市值
    ])
    empty = _page(3, [])
    monkeypatch.setattr(
        em_snapshot, "_fetch_page", lambda host, fs, page_no, page_size=1000: page if page_no == 1 else empty
    )
    df = em_snapshot.fetch_em_spot("US")
    assert list(df["code"]) == ["600001"]


def test_fetch_em_spot_failure_returns_none(monkeypatch):
    monkeypatch.setattr(em_snapshot, "_fetch_page", lambda *a, **k: None)
    assert em_snapshot.fetch_em_spot("A") is None


def test_unknown_market_raises():
    with pytest.raises(ValueError, match="unknown market"):
        em_snapshot.fetch_em_spot("XX")


def test_market_config_covers_three_markets():
    assert set(em_snapshot._MARKET_CFG) == {"A", "HK", "US"}
