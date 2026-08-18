"""委员会复核（committee）单元测试。"""

from __future__ import annotations

import pandas as pd

from chp500.committee import review

from conftest import make_snapshot


def test_review_passthrough_and_flags():
    df = make_snapshot([{}, {}])
    df["single_exceed"] = [True, False]
    df["sector_exceed"] = [False, False]
    final, summary = review(df, cfg={"committee_discretion": True})
    assert len(final) == 2 and final["committee_approved"].all()
    assert summary["n_recommended"] == 2
    assert summary["n_single_exceed"] == 1
    assert summary["n_sector_exceed"] == 0
    assert summary["committee_discretion"] is True


def test_review_empty_recommendation():
    final, summary = review(pd.DataFrame(), cfg={})
    assert final.empty
    assert summary["n_recommended"] == 0
    assert summary["n_single_exceed"] == 0


def test_review_passes_warnings_through():
    df = make_snapshot([{}])
    _, summary = review(df, cfg={}, warnings=["测试预警"])
    assert summary["warnings"] == ["测试预警"]
