"""权重计算（weight.calculator）单元测试。"""

from __future__ import annotations

import pytest

from chp500.weight.calculator import compute_weights

from conftest import make_snapshot


def test_none_mode_no_flags():
    df = make_snapshot([
        {"float_mcap": 6.0e11},
        {"float_mcap": 3.0e11},
        {"float_mcap": 1.0e11},
    ])
    out = compute_weights(df, cfg={"single_cap_mode": "none"})
    assert not out["single_exceed"].any()
    assert out["weight"].iloc[0] == pytest.approx(0.6)


def test_monitored_mode_flags_without_truncation():
    df = make_snapshot([
        {"float_mcap": 6.0e11},
        {"float_mcap": 3.0e11},
        {"float_mcap": 1.0e11},
    ])
    out = compute_weights(df, cfg={"single_cap_mode": "monitored", "cap_single": 0.10})
    assert out["weight"].iloc[0] == pytest.approx(0.6)  # 不截断
    # 0.6 与 0.3 均超 0.10 上限 -> 标记；0.1 恰好等于上限 -> 不标记
    assert out["single_exceed"].tolist() == [True, True, False]


def test_hard_mode_caps_and_preserves_total():
    df = make_snapshot([
        {"float_mcap": 6.0e11},
        {"float_mcap": 3.0e11},
        {"float_mcap": 1.0e11},
    ])
    out = compute_weights(df, cfg={"single_cap_mode": "hard", "cap_single": 0.50})
    assert out["weight"].sum() == pytest.approx(1.0)
    assert out["weight"].max() <= 0.50 + 1e-6
    assert not out["single_exceed"].any()


def test_hard_mode_with_sector_hard_cap():
    df = make_snapshot([
        {"float_mcap": 4.0e11, "sector": "金融"},
        {"float_mcap": 2.0e11, "sector": "金融"},
        {"float_mcap": 1.0e11, "sector": "信息技术"},
        {"float_mcap": 1.0e11, "sector": "信息技术"},
    ])
    cfg = {
        "single_cap_mode": "hard",
        "cap_single": 0.40,
        "sector_cap_mode": "hard",
        "max_sector_weight": 0.50,
        "convergence_tol": 1e-9,
    }
    out = compute_weights(df, cfg)
    assert out["weight"].sum() == pytest.approx(1.0, abs=1e-6)
    sector_sums = out.groupby("sector")["weight"].sum()
    assert sector_sums.max() <= 0.50 + 1e-5
    assert out["weight"].max() <= 0.40 + 1e-6


def test_unknown_mode_raises():
    df = make_snapshot([{}])
    with pytest.raises(ValueError, match="unknown single_cap_mode"):
        compute_weights(df, cfg={"single_cap_mode": "bogus"})


def test_output_sorted_by_weight_desc():
    df = make_snapshot([
        {"float_mcap": 1.0e11},
        {"float_mcap": 9.0e11},
    ])
    out = compute_weights(df, cfg={"single_cap_mode": "none"})
    assert out["weight"].is_monotonic_decreasing
    assert out.iloc[0]["float_mcap"] == 9.0e11
