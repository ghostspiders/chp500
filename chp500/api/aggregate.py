"""聚合层：读取 build_index 产物（outputs/<universe>），组装前端所需的汇总视图。

后端与前端解耦：本模块只读取落盘的 constituents.csv / index.csv / meta.json，
不依赖实时计算，便于 API 秒级响应与前端轮询。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import BASE_DIR


def _find_output_dir(universe: str) -> Path:
    base = BASE_DIR / "outputs"
    cand = base / universe
    if cand.exists():
        return cand
    # 兼容历史默认产物目录 outputs/（仅 curated）
    if universe == "curated" and base.exists():
        return base
    return cand  # 即便不存在也返回，让调用方给出 404 提示


def load_summary(universe: str) -> dict:
    out = _find_output_dir(universe)
    cons_path = out / "constituents.csv"
    idx_path = out / "index.csv"
    meta_path = out / "meta.json"
    if not cons_path.exists():
        raise FileNotFoundError(f"未找到宇宙 '{universe}' 的产物：{cons_path}（请先构建）")

    c = pd.read_csv(cons_path)
    w = c["weight"].astype(float)

    # 集中度
    top1 = float(w.max())
    top5 = float(w.nlargest(5).sum())
    top10 = float(w.nlargest(10).sum())
    top20 = float(w.nlargest(20).sum())
    hhi = float((w ** 2).sum())
    eff_n = float(1.0 / hhi) if hhi > 0 else 0.0

    # 行业
    sectors = (
        c.groupby("sector")["weight"].sum().sort_values(ascending=False)
    )
    sector_list = [{"sector": str(s), "weight": float(v)} for s, v in sectors.items()]

    # 市场
    markets = c.groupby("market")["weight"].sum().sort_values(ascending=False)
    market_list = [{"market": str(m), "weight": float(v)} for m, v in markets.items()]
    market_counts = {str(m): int(n) for m, n in c.groupby("market").size().items()}

    # Top 20
    top = c.sort_values("weight", ascending=False).head(20)
    top_list = [
        {
            "code": str(r["code"]),
            "name": str(r["name"]),
            "weight": float(r["weight"]),
            "sector": str(r.get("sector", "")),
            "market": str(r.get("market", "")),
        }
        for _, r in top.iterrows()
    ]

    # 指数序列
    idx_series = None
    if idx_path.exists():
        idx = pd.read_csv(idx_path)
        idx["date"] = pd.to_datetime(idx["date"]).dt.strftime("%Y-%m-%d")
        idx_series = {
            "dates": idx["date"].tolist(),
            "price_index": [float(x) for x in idx["price_index"].tolist()],
            "total_return": [float(x) for x in idx["total_return"].tolist()],
        }

    # 元信息
    meta = {}
    if meta_path.exists():
        import json
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # 全部成分（供明细表）
    cons_list = [
        {
            "code": str(r["code"]),
            "name": str(r["name"]),
            "weight": float(r["weight"]),
            "sector": str(r.get("sector", "")),
            "market": str(r.get("market", "")),
        }
        for _, r in c.sort_values("weight", ascending=False).iterrows()
    ]

    return {
        "universe": universe,
        "as_of": meta.get("as_of"),
        "n_constituents": int(len(c)),
        "n_universe": int(meta.get("n_universe", 0)),
        "n_eligible": int(meta.get("n_eligible", len(c))),
        "concentration": {
            "top1": top1, "top5": top5, "top10": top10, "top20": top20,
            "hhi": hhi, "effective_n": eff_n,
        },
        "sectors": sector_list,
        "markets": market_list,
        "market_counts": market_counts,
        "top": top_list,
        "constituents": cons_list,
        "index": idx_series,
        "meta": meta,
    }


def list_universes() -> list[str]:
    base = BASE_DIR / "outputs"
    if not base.exists():
        return []
    names = []
    for p in base.iterdir():
        if p.is_dir() and (p / "constituents.csv").exists():
            names.append(p.name)
    # 兼容 outputs/ 根目录（默认 curated 产物）
    if (base / "constituents.csv").exists() and "curated" not in names:
        names.append("curated")
    return sorted(names)
