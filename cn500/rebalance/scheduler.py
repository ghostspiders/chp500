"""定期再平衡与成分选择（方法论 §8）。

- 季度评审日：3/6/9/12 月最后一个交易日（简化取当月最后一日）。
- 缓冲区：现有成分排名落在 [buffer_low, buffer_high] 豁免剔除。
- 快速纳入：市值排名 <= fast_entry_rank 豁免上市时长限制。
- 目标成分 = 合格候选中按自由流通市值取前 target_count（含缓冲豁免逻辑）。
"""

from __future__ import annotations

import pandas as pd

from ..config import CONFIG


def rebalance_dates(start: str, end: str, freq: str | None = None) -> list[pd.Timestamp]:
    """生成区间内各评审日（默认季度末最后一日）。"""
    cfg = CONFIG
    freq = freq or cfg.get("rebalance_freq", "quarterly")
    months = [3, 6, 9, 12] if freq == "quarterly" else [6, 12]
    dates = pd.date_range(start, end, freq="ME")
    out = []
    for d in dates:
        if d.month in months:
            out.append(pd.Timestamp(d.year, d.month, d.days_in_month))
    return out


def select_target_constituents(
    eligible: pd.DataFrame,
    prev_constituents: pd.DataFrame | None,
    as_of,
    cfg: dict | None = None,
) -> pd.DataFrame:
    """从合格候选中产出目标成分（前 target_count，含缓冲豁免）。

    eligible: 通过 6 大准入的候选（含 weight 列，已按市值加权或仅市值排序）。
    prev_constituents: 上一期成分（含 entity_id），可为 None（首期）。
    """
    cfg = cfg or CONFIG
    target = cfg["target_count"]
    buffer_low = cfg["buffer_low"]
    buffer_high = cfg["buffer_high"]

    # 按自由流通市值降序排名
    pool = eligible.sort_values("float_mcap", ascending=False).reset_index(drop=True)
    pool["rank"] = range(1, len(pool) + 1)

    # 快速纳入豁免上市时长已由 screening 处理（fast_entry_rank 在 universe 处豁免）
    # 这里基于排名与缓冲决定纳入
    if prev_constituents is None or prev_constituents.empty:
        chosen = pool.head(target)
    else:
        prev_ids = set(prev_constituents["entity_id"])
        # 现有成分若排名在缓冲区内，豁免剔除（即便落在 target 之外）
        pool["is_prev"] = pool["entity_id"].isin(prev_ids)
        in_top = pool["rank"] <= target
        in_buffer = pool["is_prev"] & pool["rank"].between(buffer_low, buffer_high)
        pool["selected"] = in_top | in_buffer
        # 若缓冲豁免导致超过 target，仍按排名优先补齐到 target
        chosen = pool[pool["selected"]].sort_values("rank")
        if len(chosen) < target:
            extra = pool[(~pool["selected"]) & (~pool["is_prev"])].sort_values("rank").head(target - len(chosen))
            chosen = pd.concat([chosen, extra])
        chosen = chosen.sort_values("rank").head(target)
    return chosen.reset_index(drop=True)
