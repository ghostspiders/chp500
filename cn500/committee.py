"""指数委员会裁量层（方法论 §8.3）。

CN 500 非完全算法自动生成：算法产出"建议名单 + 预警"，最终成分由委员会
（或配置化的裁量规则）拍板。本项目代码不自动定稿，保留人工复核接口。
"""

from __future__ import annotations

import pandas as pd

from .config import CONFIG


def review(
    recommended: pd.DataFrame,
    cfg: dict | None = None,
    warnings: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    """对算法建议名单做委员会复核（MVP：透传 + 生成复核摘要）。

    返回 (final_constituents, review_summary)。
    真实场景中此处应有人工/委员会干预接口；本实现保留该入口并记录预警。
    """
    cfg = cfg or CONFIG
    summary = {
        "committee_discretion": cfg.get("committee_discretion", True),
        "n_recommended": len(recommended),
        "n_single_exceed": int(recommended.get("single_exceed", pd.Series([False] * len(recommended))).sum())
        if len(recommended)
        else 0,
        "n_sector_exceed": int(recommended.get("sector_exceed", pd.Series([False] * len(recommended))).sum())
        if len(recommended)
        else 0,
        "warnings": warnings,
    }
    # MVP 默认直接采纳算法建议（可在此插入人工覆盖逻辑）
    final = recommended.copy()
    final["committee_approved"] = True
    return final, summary
