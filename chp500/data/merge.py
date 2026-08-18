"""跨市场主体合并（方法论 §2 跨市场同一主体去重与映射）。

原则（与 MSCI/FTSE 全球指数一致，避免跨市场重复计入）：
  - 同一经济主体（entity_id）若同时在 A / 港股 / 中概 ADR 上市，只按
    **主上市地** 计入一次，使用其全球自由流通股数 * 主上市地现价 * 汇率折算。
  - 主上市地优先级：A > HK > US（本指数为中国本币基准）。
  - 仅在某市场挂牌的主体（如腾讯仅在港股、拼多多仅在美国），则按该市场计价并
    做多币种折算，从而真正实现「全域覆盖」。

输入：listing 级快照（每行一只证券，含 entity_id / market / curr / 市值(本币) /
市值(CNY) 等）。输出：entity 级快照（每行一个主体）。
"""

from __future__ import annotations

import pandas as pd

_PRIMARY_PRIORITY = {"A": 0, "HK": 1, "US": 2}


def merge_entities(listing: pd.DataFrame) -> pd.DataFrame:
    """将 listing 级快照按 entity_id 合并为 entity 级快照。

    实体取主上市地（A>HK>US）的市值/份额/行业；上市日期取最早；ST 取任一为 ST；
    记录上市地清单（如 "A:601318;HK:02318"）。已有 float_mcap / total_mcap
    字段假设为「按该上市地现价折算后的 CNY 市值」，实体直接沿用主上市地数值。
    """
    if listing.empty:
        return pd.DataFrame()
    df = listing.copy()
    df["_pri"] = df["market"].map(_PRIMARY_PRIORITY).fillna(99)

    rows = []
    for eid, g in df.groupby("entity_id", sort=False):
        g = g.sort_values("_pri")
        primary = g.iloc[0]
        ld = pd.to_datetime(g["listing_date"], errors="coerce").min()
        is_st = bool(g["is_st"].fillna(False).any())
        n_listings = int(len(g))
        listings = ";".join((g["market"].astype(str) + ":" + g["code"].astype(str)).tolist())

        rows.append(
            {
                "entity_id": eid,
                "code": primary["code"],
                "name": primary["name"],
                "market": primary["market"],
                "curr": primary["curr"],
                "total_shares": primary["total_shares"],
                "iwf": primary["iwf"],
                "float_shares": primary["float_shares"],
                "price": primary["price"],
                "total_mcap": primary["total_mcap"],
                "float_mcap": primary["float_mcap"],
                "ttm_net_profit": primary["ttm_net_profit"],
                "latest_q_net_profit": primary["latest_q_net_profit"],
                "sector": primary["sector"],
                "industry": primary.get("industry", primary["sector"]),
                "is_st": is_st,
                "is_china": True,
                "listing_date": ld,
                "liquidity_ratio": primary["liquidity_ratio"],
                "shares_source": primary.get("shares_source", "missing"),
                "profit_source": primary.get("profit_source", "missing"),
                "n_listings": n_listings,
                "listings": listings,
            }
        )
    return pd.DataFrame(rows)
