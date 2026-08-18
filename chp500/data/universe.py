"""扩展宇宙（演示规模推向 ~500 成分，严格真实模式）。

数据源：
  - A 股公司名/现价/TTM 净利/行业/成交量：真实（Sina + 东财 yjbb）。
  - 总股本/流通股本/IWF：东财 push2 快照（真实，需国内网络或 VPN）；
    不可达直接报错终止（绝不回落合成/静态近似）。
  - demo_universe.csv 蓝筹仅用于提供跨市场 entity_id 与上市日（保证去重），
    股本/市值一律以东财 push2 真实值为准。
"""

from __future__ import annotations

from pathlib import Path

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
from .em_snapshot import get_em_spot
from .merge import merge_entities

DATA_DIR = BASE_DIR / "data"


def apply_em_shares(df: pd.DataFrame, em: pd.DataFrame | None) -> pd.DataFrame:
    """用东财快照替换股本：总股本=总市值/价、流通股本=流通市值/价、IWF=流通/总。

    严格模式：em 不可达（None/空）直接报错，绝不回落合成/静态近似。
    个股未命中标记 shares_source="missing" 并置 NaN，由下游筛选剔除。
    """
    if em is None or em.empty:
        raise RuntimeError(
            "东财 push2（A 股快照）不可达（需国内网络/VPN）：无法获取真实市值/股本，扩展宇宙构建已终止。"
        )
    out = df.copy()
    out["shares_source"] = "missing"
    m = em.set_index("code")
    hit = out["code"].isin(m.index)
    idx = out.index[hit]
    if not len(idx):
        return out
    em_price = out.loc[idx, "code"].map(m["price"]).astype(float)
    em_tm = out.loc[idx, "code"].map(m["total_mcap_local"]).astype(float)
    em_fm = out.loc[idx, "code"].map(m["float_mcap_local"]).astype(float)
    valid = em_price > 0
    idx = idx[valid]
    em_price, em_tm, em_fm = em_price[valid], em_tm[valid], em_fm[valid]
    out.loc[idx, "price"] = em_price
    out.loc[idx, "total_shares"] = em_tm / em_price
    out.loc[idx, "float_shares"] = em_fm / em_price
    out.loc[idx, "iwf"] = em_fm / em_tm
    out.loc[idx, "total_mcap"] = em_tm  # A 股快照本币即 CNY
    out.loc[idx, "float_mcap"] = em_fm
    out.loc[idx, "shares_source"] = "em"
    # 未命中个股：无真实市值/股本，置缺失并标记，下游筛选自然剔除（不回填合成近似）。
    uncovered = out.index.difference(idx)
    if len(uncovered):
        out.loc[uncovered, ["total_shares", "float_shares", "iwf",
                            "total_mcap", "float_mcap"]] = float("nan")
    return out


def _expanded_a_listing(as_of) -> pd.DataFrame:
    as_of = pd.Timestamp(as_of)
    # 1) 真实 A 股代码/名称
    info = adapters.fetch_a_universe()[["code", "name"]].copy()
    info["code"] = info["code"].astype(str).str.zfill(6)
    info = info.sort_values("code").reset_index(drop=True)

    # 2) 真实现价
    qmap = fetch_a_quotes_sina().set_index("code")
    price = info["code"].map(qmap["price"])

    # 3) 真实 TTM 净利 / 行业
    earn = compute_ttm_earnings(fetch_a_earnings(as_of), as_of)
    ttm = info["code"].map(earn.set_index("code")["ttm_net_profit"])
    latest_q = info["code"].map(earn.set_index("code")["latest_q_net_profit"])
    industry = info["code"].map(earn.set_index("code")["industry"])

    em = get_em_spot("A", codes=info["code"].astype(str).tolist())
    if em is None or em.empty:
        raise RuntimeError(
            "东财 push2（A 股快照）不可达（需国内网络/VPN）：无法获取真实市值/股本，扩展宇宙构建已终止。"
        )

    out = pd.DataFrame({
        "entity_id": "A." + info["code"],
        "code": info["code"],
        "name": info["name"],
        "market": "A",
        "curr": "CNY",
        "price": price,
        "ttm_net_profit": ttm,
        "latest_q_net_profit": latest_q,
        "industry": industry,
        "is_st": info["name"].str.contains("ST", case=False, na=False),
        "is_china": True,
        "listing_date": pd.NaT,
    })
    out["sector"] = out["industry"].map(map_to_sector)

    # 4) 东财真实股本/市值（严格：不可达已报错；缺失标记 missing，不回落合成）
    out = apply_em_shares(out, em)

    # 5) 真实大市值蓝筹沿用 curated 参考表的 entity_id 与上市日（保证跨市场去重）；
    #    股本/市值一律以东财 push2 真实值为准（不再用参考表近似）。
    cur = pd.read_csv(DATA_DIR / "demo_universe.csv", dtype={"code": str})
    cur["code"] = cur["code"].str.zfill(6)
    for _, r in cur.iterrows():
        c = r["code"]
        mask = out["code"] == c
        if not mask.any():
            continue
        out.loc[mask, "entity_id"] = r["entity_id"]
        out.loc[mask, "listing_date"] = pd.Timestamp(r["listing_date"])
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

    from ..filter import screens
    # 粗筛（市值/股本IWF/盈利/ST/中资）：上市日与流动性均依赖 Sina 日线，
    # 故先据此筛出候选，再抓日线回填二者，避免对数千只 A 股做无谓抓取。
    # 注意：扩展宇宙 listing_date 初始为 NaT（仅蓝筹沿用 curated 参考表），
    # 真实上市日由下方 Sina 日线首日推导。
    coarse = screens.add_screen_diagnostics(entities, as_of)
    coarse_flags = ["pass_st", "pass_mcap", "pass_iwf", "pass_profit", "pass_china"]
    coarse_cand = entities.loc[coarse.index[coarse[coarse_flags].all(axis=1)]]

    # 流动性/上市日只在「有机会进入前 ~500 的候选」上计算，避免对数千只 A 股做 Sina 抓取。
    # 取按 float_mcap 降序的前 K 名（K 远大于 target_count，覆盖流动性筛选后的余量）。
    k = int(CONFIG.get("target_count", 500)) + 1100  # 1600：流动性阈值会淘汰约一半，需足够余量
    topk = coarse_cand.sort_values("float_mcap", ascending=False).head(k)

    # A 候选：抓 Sina 日线，既算流动性也算真实上市日（日线首日=上市日）
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
        # 真实上市日：Sina 日线首日（_cached_a_daily 已缓存全量历史）
        for code in codes:
            df = adapters._cached_a_daily(code)
            if df is not None and not df.empty:
                idx = a_topk.index[a_topk["code"] == code]
                entities.loc[idx, "listing_date"] = pd.Timestamp(df["date"].min())

    # HK/US 候选的上市日与流动性已在 build_hk_us_listing_snapshot 中算好，merge 已带入
    return entities


def persist_expanded_a_universe(as_of, path=None) -> pd.DataFrame:
    """落盘扩展 A 股宇宙（真实名 + 近似股本），便于审阅与复现。"""
    out = _expanded_a_listing(as_of)
    out = out[["entity_id", "code", "name", "total_shares", "iwf", "listing_date",
               "sector", "ttm_net_profit", "price", "total_mcap"]]
    path = Path(path or DATA_DIR / "demo_universe_expanded.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return out
