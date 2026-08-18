"""聚合层（api.aggregate）单元测试：基于临时 outputs 目录。"""

from __future__ import annotations

import json

import pytest

from chp500.api import aggregate


@pytest.fixture
def output_env(tmp_path, monkeypatch):
    """构造 outputs/testu/{constituents,index,meta} 产物并重定向 BASE_DIR。"""
    monkeypatch.setattr(aggregate, "BASE_DIR", tmp_path)
    out = tmp_path / "outputs" / "testu"
    out.mkdir(parents=True)

    cons = (
        "code,name,sector,market,weight\n"
        "600000,甲,金融,A,0.5\n"
        "09988,乙,信息技术,HK,0.3\n"
        "300750,丙,工业,A,0.2\n"
    )
    (out / "constituents.csv").write_text(cons, encoding="utf-8-sig")

    idx = (
        "date,price_index,total_return\n"
        "2026-08-11,1000.0,1000.0\n"
        "2026-08-12,1010.0,1010.0\n"
        "2026-08-13,1020.0,1020.0\n"
    )
    (out / "index.csv").write_text(idx, encoding="utf-8")

    meta = {"as_of": "2026-08-13", "n_universe": 50, "n_eligible": 3}
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return out


def test_load_summary_computes_concentration(output_env):
    s = aggregate.load_summary("testu")
    assert s["n_constituents"] == 3
    assert s["concentration"]["top1"] == pytest.approx(0.5)
    assert s["concentration"]["top5"] == pytest.approx(1.0)
    assert s["concentration"]["hhi"] == pytest.approx(0.25 + 0.09 + 0.04)
    assert s["concentration"]["effective_n"] == pytest.approx(1.0 / 0.38)
    assert s["as_of"] == "2026-08-13"
    assert s["n_universe"] == 50


def test_load_summary_market_and_sector_aggregation(output_env):
    s = aggregate.load_summary("testu")
    mk = {m["market"]: m["weight"] for m in s["markets"]}
    assert mk["A"] == pytest.approx(0.7) and mk["HK"] == pytest.approx(0.3)
    assert s["market_counts"] == {"A": 2, "HK": 1}
    sk = {x["sector"]: x["weight"] for x in s["sectors"]}
    assert sk["金融"] == pytest.approx(0.5)


def test_load_summary_top_ordered_and_index_series(output_env):
    s = aggregate.load_summary("testu")
    assert [t["code"] for t in s["top"]] == ["600000", "09988", "300750"]
    assert s["index"]["dates"][-1] == "2026-08-13"
    assert s["index"]["price_index"] == [1000.0, 1010.0, 1020.0]


def test_load_summary_missing_universe_404(tmp_path, monkeypatch):
    monkeypatch.setattr(aggregate, "BASE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        aggregate.load_summary("nope")


def test_load_summary_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(aggregate, "BASE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        aggregate.load_summary("../secrets")


def test_list_universes(output_env):
    names = aggregate.list_universes()
    assert names == ["testu"]


def test_top_list_capped_at_top_n(tmp_path, monkeypatch):
    monkeypatch.setattr(aggregate, "BASE_DIR", tmp_path)
    out = tmp_path / "outputs" / "big"
    out.mkdir(parents=True)
    n = 40
    rows = "".join(
        f"C{i:03d},股票{i},金融,A,{1.0 / (i + 1):.6f}\n" for i in range(n)
    )
    (out / "constituents.csv").write_text(
        "code,name,sector,market,weight\n" + rows, encoding="utf-8-sig"
    )
    s = aggregate.load_summary("big")
    assert len(s["top"]) == aggregate.TOP_N == 30
    assert len(s["constituents"]) == n  # 明细表仍为全量


def test_universe_name_validation():
    ok = ["expanded", "curated", "a-b_c1", "x" * 64]
    bad = ["../x", "a/b", "a\\b", "", "x" * 65, "空格 名", "."]
    for n in ok:
        assert aggregate.is_valid_universe_name(n), n
    for n in bad:
        assert not aggregate.is_valid_universe_name(n), n
