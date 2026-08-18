"""SEC EDGAR（edgar）单元测试：CIK 解析、概念解析、TTM 拼接与降级。"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data import edgar


class _NoCache:
    """测试用空缓存：永 miss，不做持久化。"""

    def get(self, key):
        return None

    def put(self, key, df):
        return df

    def get_or_fetch(self, key, fetcher, *a, **kw):
        return fetcher(*a, **kw)


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(edgar, "_CACHE", _NoCache())


def _concept(units: dict) -> dict:
    return {
        "cik": 1577552,
        "taxonomy": "us-gaap",
        "tag": "NetIncomeLoss",
        "units": units,
    }


def _entries(currency: str, rows: list) -> dict:
    return {
        currency: [
            {"start": s, "end": e, "val": v, "form": "20-F", "fp": "FY", "filed": "2026-05-20"}
            for s, e, v in rows
        ]
    }


def test_seed_cik_contains_adrs():
    assert edgar._SEED_CIK["BABA"] == 1577552
    assert len(edgar._SEED_CIK) >= 16


def test_fetch_us_net_income_cny(monkeypatch):
    concept = _concept(_entries("CNY", [
        ("2024-04-01", "2024-06-30", 250.0e8),
        ("2024-04-01", "2025-03-31", 1000.0e8),  # FY2025
        ("2025-04-01", "2025-06-30", 260.0e8),
        ("2025-04-01", "2026-03-31", 1100.0e8),  # FY2026
        ("2026-04-01", "2026-06-30", 300.0e8),   # Q1 FY2027 最新
    ]))
    monkeypatch.setattr(edgar, "resolve_cik", lambda t: 1577552)
    monkeypatch.setattr(edgar, "_get_json", lambda url: concept if "NetIncomeLoss" in url else None)
    res = edgar.fetch_us_net_income("BABA")
    assert res is not None
    assert res["currency"] == "CNY"
    assert res["ttm"] == (300.0e8 - 260.0e8 + 1100.0e8)
    assert res["latest_q"] > 0


def test_fetch_us_net_income_usd_converted(monkeypatch):
    concept = _concept(_entries("USD", [
        ("2024-01-01", "2024-12-31", 100.0e6),
        ("2025-01-01", "2025-12-31", 120.0e6),
    ]))
    monkeypatch.setattr(edgar, "resolve_cik", lambda t: 1737806)
    monkeypatch.setattr(edgar, "_get_json", lambda url: concept)
    res = edgar.fetch_us_net_income("PDD", usd_cny=7.0)
    assert res["currency"] == "USD"
    assert res["ttm"] == 120.0e6 * 7.0


def test_fetch_us_net_income_cny_preferred_over_usd(monkeypatch):
    concept = _concept({
        "USD": [{"start": "2025-01-01", "end": "2025-12-31", "val": 100.0}],
        "CNY": [{"start": "2025-01-01", "end": "2025-12-31", "val": 700.0}],
    })
    monkeypatch.setattr(edgar, "resolve_cik", lambda t: 1)
    monkeypatch.setattr(edgar, "_get_json", lambda url: concept)
    res = edgar.fetch_us_net_income("X", usd_cny=7.0)
    assert res["ttm"] == 700.0  # CNY 单位优先，不乘汇率


def test_fetch_us_net_income_all_failures(monkeypatch):
    monkeypatch.setattr(edgar, "resolve_cik", lambda t: None)
    assert edgar.fetch_us_net_income("NOPE") is None
    monkeypatch.setattr(edgar, "resolve_cik", lambda t: 1577552)
    monkeypatch.setattr(edgar, "_get_json", lambda url: None)  # 404
    assert edgar.fetch_us_net_income("BABA") is None


def test_ticker_map_resolution(monkeypatch):
    remote = {"0": {"cik_str": 1577552, "ticker": "baba"}}
    monkeypatch.setattr(edgar, "_get_json", lambda url: remote)
    assert edgar.resolve_cik("BABA") == 1577552
    assert edgar.resolve_cik("UNKNOWN") is None


def test_ticker_map_failure_falls_back_to_seed(monkeypatch):
    monkeypatch.setattr(edgar, "_get_json", lambda url: None)
    assert edgar.resolve_cik("JD") == edgar._SEED_CIK["JD"]
