"""基于披露期的通用 TTM 计算（港股东财财报 / 美股 SEC EDGAR 共用）。

输入为「累计口径」的净利润披露期列表 (start, end, value)：
  - 年度期（330~400 天）
  - 年内累计期（H1/Q1 等，start 均为财年起始日）

TTM = 最新期 - 去年同期 + 上一年度；最新单季 = 最新期 - 年内上一累计期。
"""

from __future__ import annotations

import pandas as pd

_FY_MIN, _FY_MAX = 300, 430  # 年度期天数窗口（财年末错位时留余量）
_TOL_SAME = 15  # 同期匹配：期长容差（天）
_TOL_END = 50  # 同期匹配：期末容差（天）


def _dedupe(periods):
    best = {}
    for start, end, value in periods:
        key = (pd.Timestamp(start), pd.Timestamp(end))
        if key not in best or pd.isna(best[key]):
            best[key] = float(value)
    return sorted(
        [(s, e, v) for (s, e), v in best.items() if pd.notna(v)],
        key=lambda x: x[1],
    )


def _is_fy(start: pd.Timestamp, end: pd.Timestamp) -> bool:
    return _FY_MIN <= (end - start).days <= _FY_MAX


def compute_ttm_from_periods(periods) -> dict | None:
    """periods: iterable of (start, end, value)。无法可靠计算时返回 None。"""
    ps = _dedupe(periods)
    if not ps:
        return None
    start, end, value = ps[-1]  # 最新披露期
    dur = (end - start).days

    if _is_fy(start, end):
        # 最新披露即年度：TTM = 年度值；单季按 1/4 近似
        return {
            "ttm": value,
            "latest_q": value / 4.0,
            "latest_end": end,
            "granularity": "year",
        }

    # 年内累计期：单季 = 最新期 - 年内上一累计期；无更短披露则按天数比例折算
    inner = None
    for s, e, v in ps[:-1]:
        if s == start and e < end:
            if inner is None or e > inner[1]:
                inner = (s, e, v)
    if inner is not None:
        latest_q = value - inner[2]
        granularity = "quarter" if (end - inner[1]).days <= 110 else "half"
    else:
        latest_q = value * 91.31 / max(dur, 1)
        granularity = "half"

    # 去年同期（期长与期末对齐）
    prior_same = None
    for s, e, v in ps[:-1]:
        if abs((e - (end - pd.Timedelta(days=365))).days) <= _TOL_END and abs((e - s).days - dur) <= _TOL_SAME + 15:
            if prior_same is None or e > prior_same[1]:
                prior_same = (s, e, v)
    # 上一完整年度（期末紧贴当前财年起始日）
    prior_fy = None
    for s, e, v in ps[:-1]:
        if _is_fy(s, e):
            if abs((e - start).days) <= _TOL_END:
                if prior_fy is None or e > prior_fy[1]:
                    prior_fy = (s, e, v)

    if prior_same is not None and prior_fy is not None:
        ttm = value - prior_same[2] + prior_fy[2]
    else:
        ttm = None

    return {
        "ttm": ttm,
        "latest_q": latest_q,
        "latest_end": end,
        "granularity": granularity,
    }
