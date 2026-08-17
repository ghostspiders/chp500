"""扩展宇宙（演示规模推向 ~500 成分）。

数据源约束（本沙箱）：
  - 可达：A 股代码/名称（stock_info_a_code_name）、现价/成交量（Sina spot）、
    净利润/行业（yjbb_em）、历史行情（Sina daily）、港股/美股（Sina daily）、汇率（中行）。
  - 不可达：东财批量市值/股本（被墙）。故**真实股本不可得**。

策略（与 demo_hk/demo_us 一致，均为 illustrative）：
  - 真实：A 股公司名、现价、TTM 净利（yjbb_em 真实）、6 个月成交量（Sina daily 真实）、行业（yjbb 真实）。
  - 近似：总股本 / IWF 由「贴近真实 A 股规模分布」的 seeded 对数正态抽样给出；
    市值 = 真实现价 × 近似股本。少数真实大市值（demo_universe.csv 中的蓝筹）沿用其
    真实近似股本与跨市场 entity_id，保证跨市场去重仍然生效。
  - 目标是演示「规模 ~500 下的行业平衡 / 集中度 / 再平衡」机制，而非真实指数点位。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import BASE_DIR, CONFIG
from ..sector.classifier import map_to_sector
from . import adapters
from .adapters import (
    fetch_a_quotes_sina,
    fetch_a_earnings,
    compute_ttm_earnings,
    fetch_hist,
    compute_liquidity,
    build_hk_us_listing_snapshot,
    fetch_hk_us_hist,
)
from . import fx
from .merge import merge_entities

DATA_DIR = BASE_DIR / "data"


def _expanded_a_listing(as_of, seed: int = 42) -> pd.DataFrame:
    as_of = pd.Timestamp(as_of)
    # 1) 真实 A 股代码/名称
    info = adapters.fetch_a_universe()[["code", "name"]].copy()
    info["code"] = info["code"].astype(str).str.zfill(6)
    info = info.sort_values("code").reset_index(drop=True)

    # 2) 真实现价 / 成交量
    qmap = fetch_a_quotes_sina().set_index("code")
    price = info["code"].map(qmap["price"])

    # 3) 真实 TTM 净利 / 行业
    earn = compute_ttm_earnings(fetch_a_earnings(as_of), as_of)
    ttm = info["code"].map(earn.set_index("code")["ttm_net_profit"])
    latest_q = info["code"].map(earn.set_index("code")["latest_q_net_profit"])
    industry = info["code"].map(earn.set_index("code")["industry"])

    # 4) 近似股本 / IWF（seeded 对数正态）。中位 ~10 亿股、sigma=0.6：使合成个股市值多落在
    #    中大盘区间（约 1500 亿 CNY 中位、99.9 分位 ~1 万亿以内），明显低于真实蓝筹（万亿级以上），
    #    既保证足够多标的跨过 400 亿市值门槛，又避免合成个股成为扭曲行业的异常巨无霸。
    #    （股本为演示近似，非真实值；真实蓝筹沿用 demo_universe.csv 的真实近似股本并主导成分前列。）
    rng = np.random.default_rng(seed)
    n = len(info)
    total_shares = rng.lognormal(mean=np.log(1.0e9), sigma=0.6, size=n)
    iwf = rng.uniform(0.15, 0.95, size=n)
    listing_offset = rng.integers(400, 6500, size=n)  # 天，绝大多数 > 12 个月

    out = pd.DataFrame({
        "entity_id": "A." + info["code"],
        "code": info["code"],
        "name": info["name"],
        "market": "A",
        "curr": "CNY",
        "total_shares": total_shares,
        "iwf": iwf,
        "price": price,
        "ttm_net_profit": ttm,
        "latest_q_net_profit": latest_q,
        "industry": industry,
        "is_st": info["name"].str.contains("ST", case=False, na=False),
        "is_china": True,
        "listing_date": [as_of - pd.Timedelta(days=int(d)) for d in listing_offset],
    })
    out["sector"] = out["industry"].map(map_to_sector)
    out["float_shares"] = out["total_shares"] * out["iwf"]
    out["total_mcap"] = out["price"] * out["total_shares"]
    out["float_mcap"] = out["price"] * out["float_shares"]
    out["liquidity_ratio"] = np.nan  # 候选阶段再算

    # 5) 真实大市值蓝筹沿用 curated 真实近似股本 + 跨市场 entity_id（保证去重）
    cur = pd.read_csv(DATA_DIR / "demo_universe.csv", dtype={"code": str})
    cur["code"] = cur["code"].str.zfill(6)
    keymap = cur.set_index("code")
    for _, r in cur.iterrows():
        c = r["code"]
        mask = out["code"] == c
        if not mask.any():
            continue
        out.loc[mask, "total_shares"] = float(r["total_shares"])
        out.loc[mask, "iwf"] = float(r["iwf"])
        out.loc[mask, "entity_id"] = r["entity_id"]
        out.loc[mask, "listing_date"] = pd.Timestamp(r["listing_date"])
        out.loc[mask, "float_shares"] = float(r["total_shares"]) * float(r["iwf"])
        out.loc[mask, "total_mcap"] = out.loc[mask, "price"] * float(r["total_shares"])
        out.loc[mask, "float_mcap"] = out.loc[mask, "price"] * out.loc[mask, "float_shares"]
    return out


def build_expanded_cross_market_snapshot(as_of, markets=None) -> pd.DataFrame:
    """扩展宇宙跨市场快照：A=全量(真实名/价/利+近似股本) + 港股/美股(参考集)。

    流动性仅在「通过其余 5 项筛选的候选」上计算，以限制 Sina daily 抓取量。
    """
    as_of = pd.Timestamp(as_of)
    markets = markets or CONFIG.get("markets", ["A", "HK", "US"])
    fx_start = (as_of - pd.Timedelta(days=400)).strftime("%Y%m%d")
    fx_end = as_of.strftime("%Y%m%d")
    fx_table = fx.fetch_fx_history(["USD", "HKD"], fx_start, fx_end)

    parts = []
    if "A" in markets:
        parts.append(_expanded_a_listing(as_of))
    if "HK" in markets:
        parts.append(build_hk_us_listing_snapshot(as_of, "HK", fx_table))
    if "US" in markets:
        parts.append(build_hk_us_listing_snapshot(as_of, "US", fx_table))
    listing = pd.concat(parts, ignore_index=True)
    entities = merge_entities(listing)

    # 候选筛选（除流动性外）：用于决定需要算流动性的子集
    from ..filter import screens
    base = screens.add_screen_diagnostics(entities, as_of)
    base_flags = ["pass_st", "pass_listing", "pass_mcap", "pass_iwf", "pass_profit", "pass_china"]
    cand_idx = base.index[base[base_flags].all(axis=1)]
    cand = entities.loc[cand_idx]

    # 流动性只在「有机会进入前 ~500 的候选」上计算，避免对数千只 A 股做 Sina 抓取。
    # 取按 float_mcap 降序的前 K 名（K 远大于 target_count，覆盖流动性筛选后的余量）。
    k = int(CONFIG.get("target_count", 500)) + 1100  # 1600：流动性阈值会淘汰约一半，需足够余量
    topk = cand.sort_values("float_mcap", ascending=False).head(k)

    # A 候选流动性（真实 Sina daily）
    a_topk = topk[topk["market"] == "A"]
    if len(a_topk):
        codes = a_topk["code"].tolist()
        start = (as_of - pd.Timedelta(days=200)).strftime("%Y%m%d")
        end = as_of.strftime("%Y%m%d")
        _, volumes = fetch_hist(codes, start, end)
        fs = pd.Series(
            (a_topk.set_index("code")["total_shares"] * a_topk.set_index("code")["iwf"]).values,
            index=a_topk["code"].values,
        )
        liq = compute_liquidity(volumes, fs)
        entities.loc[a_topk.index, "liquidity_ratio"] = a_topk["code"].map(liq)

    # HK/US 候选流动性已在 build_hk_us_listing_snapshot 中算好，merge 已带入
    return entities


def persist_expanded_a_universe(as_of, path=None) -> pd.DataFrame:
    """落盘扩展 A 股宇宙（真实名 + 近似股本），便于审阅与复现。"""
    out = _expanded_a_listing(as_of)
    out = out[["entity_id", "code", "name", "total_shares", "iwf", "listing_date",
               "sector", "ttm_net_profit", "price", "total_mcap"]]
    path = Path(path or DATA_DIR / "demo_universe_expanded.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return out
