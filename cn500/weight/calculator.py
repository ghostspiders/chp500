"""权重计算（方法论 §7）：自由流通市值加权 + 个股权重上限三种模式。

single_cap_mode:
  - "none"       : 不限制（对标标普，仅后续监控）
  - "monitored"  : 不截断，仅标记 single_exceed（> cap_single）供委员会复核
  - "hard"       : 迭代再分配，使单只权重不超过 cap_single（可选叠加行业硬上限）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import CONFIG


def _iterative_cap(
    weights: np.ndarray, cap: float, groups: np.ndarray | None, group_cap: float | None, tol: float
) -> np.ndarray:
    """将越界权重按比例 redistribute 给未达上限者，直至收敛。"""
    w = weights.copy().astype(float)
    n = len(w)
    for _ in range(10000):
        changed = False
        # 个股上限
        over = w > cap + tol
        if over.any():
            excess = (w[over] - cap).sum()
            w[over] = cap
            remaining = ~over
            if remaining.any():
                w[remaining] += excess * (w[remaining] / w[remaining].sum())
            changed = True
        # 行业上限
        if groups is not None and group_cap is not None:
            for g in np.unique(groups):
                idx = groups == g
                s = w[idx].sum()
                if s > group_cap + tol:
                    excess = s - group_cap
                    w[idx] = w[idx] / s * group_cap
                    remaining = ~idx
                    if remaining.any():
                        w[remaining] += excess * (w[remaining] / w[remaining].sum())
                    changed = True
        if not changed:
            break
    return w


def compute_weights(df: pd.DataFrame, cfg: dict | None = None, mcap_col: str = "float_mcap") -> pd.DataFrame:
    """计算权重并返回带 weight / single_exceed 的表（按权重降序）。"""
    cfg = cfg or CONFIG
    out = df.copy()
    total = out[mcap_col].sum()
    out["weight"] = out[mcap_col] / total if total > 0 else 0.0

    mode = cfg.get("single_cap_mode", "monitored")
    cap = cfg.get("cap_single", 0.10)
    tol = cfg.get("convergence_tol", 1e-6)

    if mode == "none":
        out["single_exceed"] = False
    elif mode == "monitored":
        out["single_exceed"] = out["weight"] > cap
    elif mode == "hard":
        groups = None
        group_cap = None
        if cfg.get("sector_cap_mode") == "hard":
            groups = out["sector"].values
            group_cap = cfg.get("max_sector_weight", 0.20)
        w = _iterative_cap(out["weight"].values, cap, groups, group_cap, tol)
        out["weight"] = w
        out["single_exceed"] = out["weight"] > cap + tol
    else:
        raise ValueError(f"unknown single_cap_mode: {mode}")

    return out.sort_values("weight", ascending=False).reset_index(drop=True)
