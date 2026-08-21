"""聚合层：读取 build_index 产物（outputs/<universe>），组装前端所需的汇总视图。

后端与前端解耦：本模块只读取落盘的 constituents.csv / index.csv / meta.json，
不依赖实时计算，便于 API 秒级响应与前端轮询。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import BASE_DIR
from ..index import persistent as pidx
from ..data import benchmark as benchmod

# 宇宙名会拼进文件系统路径，必须限定为安全的短标识符
UNIVERSE_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_UNIVERSE_RE = re.compile(UNIVERSE_NAME_PATTERN)

# 权重榜首数量（前端 TOP 榜图表取该列表渲染）
TOP_N = 30


def is_valid_universe_name(name: str) -> bool:
    return bool(_UNIVERSE_RE.fullmatch(name))


def _find_output_dir(universe: str) -> Path:
    if not is_valid_universe_name(universe):
        raise FileNotFoundError(f"非法宇宙名：{universe!r}")
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

    # code 保持字符串，避免港股代码前导零（如 09988）被读成整数
    c = pd.read_csv(cons_path, dtype={"code": str})
    w = c["weight"].astype(float)

    # 集中度
    top1 = float(w.max())
    top5 = float(w.nlargest(5).sum())
    top10 = float(w.nlargest(10).sum())
    top20 = float(w.nlargest(20).sum())
    top30 = float(w.nlargest(30).sum())
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

    # Top N 权重榜
    top = c.sort_values("weight", ascending=False).head(TOP_N)
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

    # 数据源覆盖（腾讯行情/EDGAR 真实数据 vs 缺失）
    src = {"shares": {}, "profit": {}}
    if "shares_source" in c.columns:
        src["shares"] = {str(k): int(v) for k, v in c["shares_source"].value_counts().items()}
    if "profit_source" in c.columns:
        src["profit"] = {str(k): int(v) for k, v in c["profit_source"].value_counts().items()}
    n_all = max(len(c), 1)
    # 真实市值/股本来源：腾讯快照（落盘标记曾用 "em"，新版用 "tencent"），两者均视为真实。
    real_shares_n = src["shares"].get("tencent", 0) + src["shares"].get("em", 0)
    real_profit_n = src["profit"].get("tencent", 0) + src["profit"].get("em", 0) + src["profit"].get("edgar", 0)
    real_shares = real_shares_n / n_all
    real_profit = real_profit_n / n_all

    return {
        "universe": universe,
        "as_of": meta.get("as_of"),
        "n_constituents": int(len(c)),
        "n_universe": int(meta.get("n_universe", 0)),
        "n_eligible": int(meta.get("n_eligible", len(c))),
        "concentration": {
            "top1": top1, "top5": top5, "top10": top10, "top20": top20, "top30": top30,
            "hhi": hhi, "effective_n": eff_n,
        },
        "sectors": sector_list,
        "markets": market_list,
        "market_counts": market_counts,
        "top": top_list,
        "constituents": cons_list,
        "index": idx_series,
        "meta": meta,
        "data_sources": src,
        "real_shares_ratio": real_shares,
        "real_profit_ratio": real_profit,
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


# ---------------------------------------------------------------------------
# 常年运行：连续指数 / 再平衡历史 / 运行日志 / 基准（读取自 SQLite）
# ---------------------------------------------------------------------------

def _db_path(universe: str) -> Path:
    if not is_valid_universe_name(universe):
        raise FileNotFoundError(f"非法宇宙名：{universe!r}")
    p = BASE_DIR / "outputs" / universe / "chp500.db"
    if not p.exists():
        raise FileNotFoundError(
            f"未找到宇宙 '{universe}' 的数据库：{p}（请先运行构建以建库）")
    return p


def load_index_history(universe: str, from_date: str | None = None,
                       to_date: str | None = None) -> pd.DataFrame:
    """连续指数净值序列（可限区间）。"""
    con = sqlite3.connect(str(_db_path(universe)))
    sql = ("SELECT date, price_index, total_return, divisor, rebalance_as_of "
           "FROM index_levels")
    where, params = [], []
    if from_date:
        where.append("date >= ?"); params.append(from_date)
    if to_date:
        where.append("date <= ?"); params.append(to_date)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date"
    df = pd.read_sql_query(sql, con, params=params)
    con.close()
    return df


def list_rebalances(universe: str) -> list[dict]:
    con = pidx.open_db(_db_path(universe))
    return pidx.list_rebalances(con)


def load_rebalance(universe: str, as_of: str) -> list[dict]:
    con = pidx.open_db(_db_path(universe))
    cols = [r[1] for r in con.execute("PRAGMA table_info(rebalances)").fetchall()]
    rows = con.execute("SELECT * FROM rebalances WHERE as_of = ? ORDER BY weight DESC",
                       (as_of,)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def load_runs(universe: str) -> list[dict]:
    con = pidx.open_db(_db_path(universe))
    cols = ["id", "as_of", "kind", "started_at", "finished_at",
            "status", "n_constituents", "message"]
    rows = con.execute(
        "SELECT id, as_of, kind, started_at, finished_at, status, "
        "n_constituents, message FROM runs ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def list_benchmarks(universe: str) -> list[dict]:
    con = pidx.open_db(_db_path(universe))
    out = []
    for b in benchmod.list_benchmarks(con):
        s = benchmod.benchmark_series(con, b)
        out.append({
            "bench_id": b,
            "n": int(len(s)),
            "start": s["date"].min() if not s.empty else None,
            "end": s["date"].max() if not s.empty else None,
        })
    return out


def load_benchmark_series(universe: str, bench_id: str) -> pd.DataFrame:
    con = pidx.open_db(_db_path(universe))
    return benchmod.benchmark_series(con, bench_id)
