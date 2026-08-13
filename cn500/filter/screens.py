"""成分股入选准入筛选（方法论 §5）与剔除检查（§8.2）。

所有函数接收一份"快照"DataFrame（每行一只证券），列至少包含：
    entity_id, code, name, market, is_st, listing_date,
    total_mcap, float_mcap, iwf, ttm_net_profit, latest_q_net_profit,
    liquidity_ratio, is_china
并返回布尔 Series 或带原因的诊断表。
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..config import CONFIG


def add_screen_diagnostics(df: pd.DataFrame, as_of: datetime, cfg: dict | None = None) -> pd.DataFrame:
    """对快照逐条计算 6 大准入指标是否通过，并给出未通过原因。"""
    cfg = cfg or CONFIG
    out = df.copy()
    as_of = pd.Timestamp(as_of)

    # 1) ST / 可投资性
    out["pass_st"] = ~out["is_st"].fillna(False)
    # 2) 上市时长
    listing = pd.to_datetime(out["listing_date"], errors="coerce")
    months = (as_of - listing).dt.days / 30.4375
    out["pass_listing"] = months >= cfg["listing_min_months"]
    # 3) 市值门槛（未调整总市值）
    out["pass_mcap"] = out["total_mcap"] >= cfg["mcap_min"]
    # 4) 自由流通比例
    out["pass_iwf"] = out["iwf"] >= cfg["iwf_min"]
    # 5) 盈利门槛
    out["pass_profit"] = (out["ttm_net_profit"] > 0) & (out["latest_q_net_profit"] > 0)
    # 6) 流动性（跨市场分市场阈值：A 股换手率显著高于港股/美股，统一 1.0 会把多数
    #    中资港股/中概 ADR 误杀；故按市场分别设下限，更贴近真实跨市场指数做法）
    liq_min_by_mkt = cfg.get("liquidity_ratio_min_by_market", {}) or {}
    liq_thr = out["market"].map(lambda m: liq_min_by_mkt.get(m, cfg["liquidity_ratio_min"]))
    out["pass_liquidity"] = out["liquidity_ratio"] >= liq_thr
    # 中国公司（跨市场情形由 universe 模块保证）
    out["pass_china"] = out.get("is_china", pd.Series(True, index=out.index)).fillna(True)

    reasons = []
    for _, r in out.iterrows():
        fails = []
        if not r["pass_st"]:
            fails.append("ST/可投资性")
        if not r["pass_listing"]:
            fails.append("上市不足")
        if not r["pass_mcap"]:
            fails.append("市值不足")
        if not r["pass_iwf"]:
            fails.append("IWF不足")
        if not r["pass_profit"]:
            fails.append("盈利不达标")
        if not r["pass_liquidity"]:
            fails.append("流动性不足")
        if not r["pass_china"]:
            fails.append("非中国公司")
        reasons.append(";".join(fails))
    out["fail_reasons"] = reasons
    out["eligible"] = out[
        ["pass_st", "pass_listing", "pass_mcap", "pass_iwf", "pass_profit", "pass_liquidity", "pass_china"]
    ].all(axis=1)
    return out


def select_eligible(df: pd.DataFrame, as_of: datetime, cfg: dict | None = None) -> pd.DataFrame:
    """返回通过全部 6 大准入指标的候选池。"""
    diag = add_screen_diagnostics(df, as_of, cfg)
    return diag[diag["eligible"]].copy()


def check_deletion(
    df: pd.DataFrame, as_of: datetime, cfg: dict | None = None
) -> pd.DataFrame:
    """对现有成分做剔除检查（方法论 §8.2），返回带 delist 标志的表。

    输入应为"当前成分"快照（含 is_constituent=True）。
    """
    cfg = cfg or CONFIG
    out = df.copy()
    as_of = pd.Timestamp(as_of)

    severe_loss = out["ttm_net_profit"] <= 0  # 财务严重恶化（TTM 转负）
    mcap_shrink = out["total_mcap"] < cfg["mcap_min"]
    iwf_drop = out["iwf"] < cfg["iwf_delist_threshold"]
    st_now = out["is_st"].fillna(False)
    not_china = ~out.get("is_china", pd.Series(True, index=out.index)).fillna(True)

    out["delist_reason"] = ""
    reason = np.where(st_now, "ST/退市", "")
    reason = np.where(severe_loss & (reason == ""), "财务恶化", reason)
    reason = np.where(mcap_shrink & (reason == ""), "市值缩水", reason)
    reason = np.where(iwf_drop & (reason == ""), "IWF跌破", reason)
    reason = np.where(not_china & (reason == ""), "非中国", reason)
    out["delist_reason"] = reason
    out["delist"] = out["delist_reason"] != ""
    return out
