"""基于披露期的 TTM 计算（ttm_periods）单元测试。

样例对齐腾讯真实披露节奏：FY(1-12月) + H1(1-6月) + Q1(1-3月)。
"""

from __future__ import annotations

import pandas as pd

from chp500.data.ttm_periods import compute_ttm_from_periods


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


def test_tencent_style_quarterly_disclosure():
    periods = [
        (_ts("2023-01-01"), _ts("2023-12-31"), 1152.16e8),
        (_ts("2024-01-01"), _ts("2024-12-31"), 1940.73e8),
        (_ts("2024-01-01"), _ts("2024-06-30"), 895.0e8),
        (_ts("2025-01-01"), _ts("2025-12-31"), 2248.42e8),
        (_ts("2025-01-01"), _ts("2025-06-30"), 1088.0e8),
        (_ts("2026-01-01"), _ts("2026-03-31"), 580.93e8),
        (_ts("2026-01-01"), _ts("2026-06-30"), 1141.15e8),
    ]
    res = compute_ttm_from_periods(periods)
    # TTM = H1(2026) - H1(2025) + FY(2025)
    assert res["ttm"] == 1141.15e8 - 1088.0e8 + 2248.42e8
    # 最新单季 = H1(2026) - Q1(2026)
    assert res["latest_q"] == 1141.15e8 - 580.93e8
    assert res["granularity"] == "quarter"
    assert res["latest_end"] == _ts("2026-06-30")


def test_annual_latest():
    periods = [
        (_ts("2024-01-01"), _ts("2024-12-31"), 100.0),
        (_ts("2025-01-01"), _ts("2025-12-31"), 120.0),
    ]
    res = compute_ttm_from_periods(periods)
    assert res["ttm"] == 120.0
    assert res["latest_q"] == 30.0  # 年度值按 1/4 近似
    assert res["granularity"] == "year"


def test_half_year_only_filer():
    periods = [
        (_ts("2024-01-01"), _ts("2024-12-31"), 100.0),
        (_ts("2024-01-01"), _ts("2024-06-30"), 40.0),
        (_ts("2025-01-01"), _ts("2025-12-31"), 120.0),
        (_ts("2025-01-01"), _ts("2025-06-30"), 50.0),
        (_ts("2026-01-01"), _ts("2026-06-30"), 60.0),
    ]
    res = compute_ttm_from_periods(periods)
    # TTM = H1(2026) - H1(2025) + FY(2025)
    assert res["ttm"] == 60.0 - 50.0 + 120.0
    # 无季度披露：按天数比例折算单季（1/1..6/30 差分为 180 天）
    assert res["latest_q"] == 60.0 * 91.31 / 180
    assert res["granularity"] == "half"


def test_fiscal_year_offset_march_year_end():
    # 阿里式 3 月财年：FY(4月-次年3月) + 半年(4-9月) + 季(4-6月)
    periods = [
        (_ts("2024-04-01"), _ts("2025-03-31"), 1301.09e8),  # FY2025
        (_ts("2024-04-01"), _ts("2024-09-30"), 700.0e8),   # H1 FY2025
        (_ts("2025-04-01"), _ts("2026-03-31"), 1035.92e8),  # FY2026
        (_ts("2025-04-01"), _ts("2025-09-30"), 650.0e8),   # H1 FY2026
        (_ts("2026-04-01"), _ts("2026-06-30"), 300.0e8),   # Q1 FY2027（财年首季，无年内更短披露）
    ]
    res = compute_ttm_from_periods(periods)
    # 首季 dur=90 天，无年内更短披露 -> 按比例折算
    assert res["ttm"] is None
    assert res["latest_q"] == 300.0e8 * 91.31 / 90
    assert res["granularity"] == "half"


def test_fiscal_offset_with_prior_quarter():
    periods = [
        (_ts("2024-04-01"), _ts("2025-03-31"), 1000.0),
        (_ts("2024-04-01"), _ts("2024-06-30"), 250.0),
        (_ts("2025-04-01"), _ts("2026-03-31"), 1100.0),
        (_ts("2025-04-01"), _ts("2025-06-30"), 260.0),
        (_ts("2025-04-01"), _ts("2025-09-30"), 530.0),
        (_ts("2026-04-01"), _ts("2026-06-30"), 300.0),
    ]
    res = compute_ttm_from_periods(periods)
    # 最新 = Q1 FY2027(300)；同期 = Q1 FY2026(260)；上一年度 = FY2026(1100)
    assert res["ttm"] == 300.0 - 260.0 + 1100.0
    # 年内无更短披露 -> 按比例折算
    assert res["granularity"] == "half"


def test_duplicate_periods_deduped():
    periods = [
        (_ts("2025-01-01"), _ts("2025-12-31"), 100.0),
        (_ts("2025-01-01"), _ts("2025-12-31"), 999.0),  # 重复披露，保留首条
        (_ts("2026-01-01"), _ts("2026-06-30"), 60.0),
        (_ts("2025-01-01"), _ts("2025-06-30"), 50.0),
    ]
    res = compute_ttm_from_periods(periods)
    assert res["ttm"] == 60.0 - 50.0 + 100.0


def test_empty_periods():
    assert compute_ttm_from_periods([]) is None
