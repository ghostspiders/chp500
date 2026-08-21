"""连续指数存储与推进（常年运行核心）。

取代 build_index 中「每次重算近一年、基准重置」的覆盖式 index.csv，改为：
- 固定基期（inception_date, base_point）一次性建库；
- 每日按当前篮子补点净值（divisor 不变）；
- 每次篮子变动（再平衡 / 周度 IWF 刷新 / 公司行为）在过渡日调 divisor
  （rebase_divisor）保持指数严格连续、无跳空。

存储：outputs/<universe>/chp500.db （见 _SCHEMA）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import series as idx
from ..config import BASE_DIR

# ---------------------------------------------------------------------------
# 路径与建表
# ---------------------------------------------------------------------------

def db_path_for(universe: str, outputs_root: Path | None = None) -> Path:
    root = outputs_root or (BASE_DIR / "outputs")
    return root / universe / "chp500.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rebalances (
  as_of TEXT NOT NULL,
  effective_date TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  code TEXT, name TEXT, market TEXT, sector TEXT, industry TEXT,
  price REAL, total_shares REAL, float_shares REAL,
  float_mcap REAL, iwf REAL,
  ttm_net_profit REAL, liquidity_ratio REAL,
  weight REAL,
  shares_source TEXT, profit_source TEXT,
  PRIMARY KEY (as_of, entity_id)
);
CREATE TABLE IF NOT EXISTS index_levels (
  date TEXT PRIMARY KEY,
  price_index REAL NOT NULL,
  total_return REAL NOT NULL,
  divisor REAL NOT NULL,
  rebalance_as_of TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS benchmarks (
  bench_id TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  PRIMARY KEY (bench_id, date)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of TEXT,
  kind TEXT,
  started_at TEXT,
  finished_at TEXT,
  status TEXT,
  n_constituents INTEGER,
  message TEXT
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL;")
    con.executescript(_SCHEMA)
    return con


# ---------------------------------------------------------------------------
# 读取辅助
# ---------------------------------------------------------------------------

def last_level(con: sqlite3.Connection) -> dict | None:
    """最近一条指数净值（含 divisor 与生效的再平衡）。"""
    row = con.execute(
        "SELECT date, price_index, total_return, divisor, rebalance_as_of "
        "FROM index_levels ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "date": pd.Timestamp(row[0]),
        "price_index": float(row[1]),
        "total_return": float(row[2]),
        "divisor": float(row[3]),
        "rebalance_as_of": row[4],
    }


def level_on_or_before(con: sqlite3.Connection, date: pd.Timestamp) -> dict | None:
    """取 <= date 的最近一条净值（用于再平衡过渡日的前一日衔接）。"""
    d = date.strftime("%Y-%m-%d")
    row = con.execute(
        "SELECT date, price_index, divisor, rebalance_as_of FROM index_levels "
        "WHERE date <= ? ORDER BY date DESC LIMIT 1", (d,)
    ).fetchone()
    if not row:
        return None
    return {
        "date": pd.Timestamp(row[0]),
        "price_index": float(row[1]),
        "divisor": float(row[2]),
        "rebalance_as_of": row[3],
    }


def latest_rebalance_as_of(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT MAX(as_of) FROM rebalances").fetchone()
    return row[0] if row and row[0] else None


def current_basket(con: sqlite3.Connection) -> pd.DataFrame | None:
    """最近一次再平衡的成分篮子（含 float_shares / market / curr）。"""
    as_of = latest_rebalance_as_of(con)
    if not as_of:
        return None
    rows = con.execute(
        "SELECT entity_id, code, name, market, sector, industry, "
        "float_shares, float_mcap, weight, shares_source, profit_source "
        "FROM rebalances WHERE as_of = ?", (as_of,)
    ).fetchall()
    if not rows:
        return None
    cols = ["entity_id", "code", "name", "market", "sector", "industry",
            "float_shares", "float_mcap", "weight", "shares_source", "profit_source"]
    return pd.DataFrame(rows, columns=cols)


def list_rebalances(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT as_of, effective_date, COUNT(*) FROM rebalances "
        "GROUP BY as_of ORDER BY as_of"
    ).fetchall()
    return [{"as_of": r[0], "effective_date": r[1], "n": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# 写入辅助
# ---------------------------------------------------------------------------

def upsert_rebalances(con, basket: pd.DataFrame, as_of: str, effective_date: str) -> None:
    cols = ["entity_id", "code", "name", "market", "sector", "industry",
            "price", "total_shares", "float_shares", "float_mcap", "iwf",
            "ttm_net_profit", "liquidity_ratio", "weight",
            "shares_source", "profit_source"]
    have = [c for c in cols if c in basket.columns]
    df = basket[have].copy()
    df["as_of"] = as_of
    df["effective_date"] = effective_date
    # 删除该 as_of 的旧快照（幂等重跑）
    con.execute("DELETE FROM rebalances WHERE as_of = ?", (as_of,))
    recs = []
    for _, r in df.iterrows():
        recs.append((
            as_of, effective_date,
            str(r.get("entity_id", "")), _nil(r.get("code")), _nil(r.get("name")),
            _nil(r.get("market")), _nil(r.get("sector")), _nil(r.get("industry")),
            _num(r.get("price")), _num(r.get("total_shares")), _num(r.get("float_shares")),
            _num(r.get("float_mcap")), _num(r.get("iwf")), _num(r.get("ttm_net_profit")),
            _num(r.get("liquidity_ratio")), _num(r.get("weight")),
            _nil(r.get("shares_source")), _nil(r.get("profit_source")),
        ))
    con.executemany(
        "INSERT OR REPLACE INTO rebalances "
        "(as_of, effective_date, entity_id, code, name, market, sector, industry, "
        " price, total_shares, float_shares, float_mcap, iwf, ttm_net_profit, "
        " liquidity_ratio, weight, shares_source, profit_source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", recs)
    con.commit()


def upsert_levels(con, df: pd.DataFrame) -> None:
    """df 含 date(字符串), price_index, total_return, divisor, rebalance_as_of, note。"""
    recs = [
        (r["date"], float(r["price_index"]), float(r["total_return"]),
         float(r["divisor"]), _nil(r.get("rebalance_as_of")), _nil(r.get("note")))
        for _, r in df.iterrows()
    ]
    con.executemany(
        "INSERT OR REPLACE INTO index_levels "
        "(date, price_index, total_return, divisor, rebalance_as_of, note) "
        "VALUES (?,?,?,?,?,?)", recs)
    con.commit()


def replace_levels_from(con, df: pd.DataFrame, from_date: pd.Timestamp) -> None:
    """删除 >= from_date 的净值后重新写入（用于再平衡/篮子变动的历史修正）。"""
    con.execute("DELETE FROM index_levels WHERE date >= ?",
                (from_date.strftime("%Y-%m-%d"),))
    upsert_levels(con, df)


def record_run(con, as_of: str, kind: str, status: str,
               n_constituents: int | None = None, message: str = "") -> int:
    now = datetime.now().isoformat(timespec="seconds")
    cur = con.execute(
        "INSERT INTO runs (as_of, kind, started_at, finished_at, status, "
        "n_constituents, message) VALUES (?,?,?,?,?,?,?)",
        (as_of, kind, now, now, status, n_constituents, message))
    con.commit()
    return int(cur.lastrowid)


def _nil(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return str(v)


def _num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v)


# ---------------------------------------------------------------------------
# 价格抓取（跨市场折算 CNY，复用 adapters + 清洗）
# ---------------------------------------------------------------------------

def _clean_price(ser: pd.Series) -> pd.Series:
    """剔除坏点：0 值，以及「单日跳变超 30% 且次日回到跳变前水平(±10%)」的异常尖刺。"""
    ser = ser.replace(0, np.nan)
    ret = ser.pct_change()
    jump = ret.abs() > 0.30
    revert = (ser.shift(-1) / ser.shift(1) - 1.0).abs() < 0.10
    spike = jump & revert.fillna(False)
    ser = ser.mask(spike, ser.shift(1))
    return ser.ffill().bfill()


def fetch_cny_prices(basket: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """取篮子的 CNY 每股价格序列（索引=日期，列=entity_id）。

    - A 股价格本币即 CNY；HK/US 经中行汇率折算 CNY；
    - 复用 adapters 的按市场历史行情（已按 code 全量缓存）；
    - 个股最新已知价向前填充对齐（指数编制标准做法），并做坏点清洗。
    """
    start_s = pd.Timestamp(start)
    end_s = pd.Timestamp(end)
    s, e = start_s.strftime("%Y%m%d"), end_s.strftime("%Y%m%d")
    # 延迟导入：避免在无 akshare 环境（如单测）强制加载数据适配层
    from ..data import adapters
    from ..data import fx as fxmod
    fxs = fxmod.fetch_fx_history(["USD", "HKD"], s, e)
    code_to_eid = {(str(r["market"]), str(r["code"])): r["entity_id"]
                   for _, r in basket.iterrows()}

    parts: list[pd.DataFrame] = []
    a = basket[basket["market"] == "A"]
    hk = basket[basket["market"] == "HK"]
    us = basket[basket["market"] == "US"]
    if len(a):
        p, _ = adapters.fetch_hist(a["code"].tolist(), s, e)
        if not p.empty:
            p = p.rename(columns={( "A", c): code_to_eid[("A", c)] for c in p.columns})
            parts.append(p)
    if len(hk):
        p, _ = adapters.fetch_hk_us_hist(hk["code"].tolist(), "HK", s, e)
        if not p.empty:
            fx = fxs.get("HKD", pd.Series(1.0, index=p.index))
            fx = fx.reindex(p.index).ffill().bfill().fillna(1.0)
            p = (p * fx).rename(columns={c: code_to_eid[("HK", c)] for c in p.columns})
            parts.append(p)
    if len(us):
        p, _ = adapters.fetch_hk_us_hist(us["code"].tolist(), "US", s, e)
        if not p.empty:
            fx = fxs.get("USD", pd.Series(1.0, index=p.index))
            fx = fx.reindex(p.index).ffill().bfill().fillna(1.0)
            p = (p * fx).rename(columns={c: code_to_eid[("US", c)] for c in p.columns})
            parts.append(p)

    if not parts:
        return pd.DataFrame()
    prices = pd.concat(parts, axis=1)
    prices = prices.dropna(how="all").sort_index()
    prices = prices.ffill()  # 跨市场缺失交易日向前对齐
    prices = prices.apply(_clean_price) if not prices.empty else prices
    return prices


# ---------------------------------------------------------------------------
# 指数推进
# ---------------------------------------------------------------------------

def _compute_levels(prices: pd.DataFrame, float_shares: pd.Series,
                    divisor: float, rebalance_as_of: str, note: str = "") -> pd.DataFrame:
    common = [c for c in prices.columns if c in float_shares.index]
    if not common:
        return pd.DataFrame(columns=["date", "price_index", "total_return",
                                     "divisor", "rebalance_as_of", "note"])
    p = prices[common]
    fs = float_shares[common]
    pi = idx.price_index(p, fs, divisor)
    tri = idx.total_return_index(p, fs, divisor)  # 无分红 -> 与 price_index 相同
    df = pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in p.index],
        "price_index": pi.values,
        "total_return": tri.values,
        "divisor": divisor,
        "rebalance_as_of": rebalance_as_of,
        "note": note,
    })
    return df


def ensure_inception(con, basket: pd.DataFrame, as_of: str,
                     inception_date: str, base_point: float = 1000.0) -> pd.DataFrame:
    """首次建库：从 inception_date 到 as_of，固定基期 base_point，写入净值与再平衡快照。"""
    prices = fetch_cny_prices(basket, inception_date, as_of)
    if prices.empty:
        raise RuntimeError("建库失败：inception_date 起无任何行情数据。")
    fs = basket.set_index("entity_id")["float_shares"]
    first_mv = (prices.iloc[0] * fs).sum()
    if first_mv <= 0:
        raise RuntimeError("建库失败：基期总自由流通市值为非正。")
    divisor = idx.initial_divisor(float(first_mv), base_point)
    levels = _compute_levels(prices, fs, divisor, as_of, note="inception")
    upsert_rebalances(con, basket, as_of, inception_date)
    replace_levels_from(con, levels, pd.Timestamp(inception_date))
    return levels


def append_daily(con, basket: pd.DataFrame, as_of: str) -> pd.DataFrame | None:
    """按当前篮子补点净值（divisor 不变）。返回新增的净值行（如有）。"""
    last = last_level(con)
    if last is None:
        return None  # 尚未建库，应走 ensure_inception
    start = (last["date"] + pd.Timedelta(days=1))
    end = pd.Timestamp(as_of)
    if start > end:
        return None
    prices = fetch_cny_prices(basket, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if prices.empty:
        return None
    fs = basket.set_index("entity_id")["float_shares"]
    levels = _compute_levels(prices, fs, last["divisor"], last["rebalance_as_of"], note="daily")
    upsert_levels(con, levels)
    return levels


def apply_basket_change(con, new_basket: pd.DataFrame, as_of: str,
                        kind: str = "rebalance") -> pd.DataFrame:
    """篮子变动（再平衡 / 周度 IWF 刷新）：在过渡日调 divisor 保持连续。

    返回重新计算并写入的净值区间。
    """
    last = last_level(con)
    if last is None:
        # 尚无历史 -> 视为建库
        return ensure_inception(con, new_basket, as_of, as_of, base_point=1000.0)

    prices = fetch_cny_prices(new_basket, as_of, as_of)  # 先探查过渡日
    if prices.empty:
        # 过渡日尚无行情（如节假日），改为补到今天
        prices = fetch_cny_prices(new_basket, as_of,
                                  pd.Timestamp.now().strftime("%Y-%m-%d"))
    if prices.empty:
        return pd.DataFrame(columns=["date", "price_index", "total_return",
                                     "divisor", "rebalance_as_of", "note"])
    T = prices.index[0]  # 第一个有行情的过渡日
    fs = new_basket.set_index("entity_id")["float_shares"]

    prev = level_on_or_before(con, T - pd.Timedelta(days=1))
    if prev is None:
        # 过渡日之前无历史 -> 当作建库（基期 = T）
        levels = ensure_inception(con, new_basket, as_of, T.strftime("%Y-%m-%d"),
                                  base_point=1000.0)
        return levels

    # 以过渡日新篮子总市值 / 前一日净值 重定除数，使指数在 T 连续无跳空
    new_mv_T = (prices.loc[T] * fs).sum()
    new_divisor = idx.rebase_divisor(prev["price_index"], float(new_mv_T))

    # 计算 [T, today] 全区间（覆盖可能已用旧篮子写入的 [T, today]）
    end = pd.Timestamp.now().strftime("%Y-%m-%d")
    prices_full = fetch_cny_prices(new_basket, T.strftime("%Y-%m-%d"), end)
    levels = _compute_levels(prices_full, fs, new_divisor, as_of, note=kind)
    upsert_rebalances(con, new_basket, as_of, T.strftime("%Y-%m-%d"))
    replace_levels_from(con, levels, T)
    return levels


# ---------------------------------------------------------------------------
# 高层入口（供 build_index / API 调用）
# ---------------------------------------------------------------------------

def update_index(con, basket: pd.DataFrame, as_of: str,
                 kind: str = "rebalance", inception_date: str | None = None,
                 base_point: float = 1000.0) -> dict:
    """推进指数：建库或篮子变动。返回状态摘要。"""
    rid = record_run(con, as_of, kind, "running", n_constituents=int(len(basket)))
    try:
        if last_level(con) is None:
            inc = inception_date or (pd.Timestamp(as_of) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
            levels = ensure_inception(con, basket, as_of, inc, base_point)
            msg = f"建库：基期 {inc}，{len(levels)} 个交易日"
        elif kind == "daily":
            levels = append_daily(con, basket, as_of)
            msg = f"补点：新增 {0 if levels is None else len(levels)} 个交易日"
        else:
            levels = apply_basket_change(con, basket, as_of, kind)
            msg = f"{kind}：重算 {len(levels)} 个交易日"
        con.execute("UPDATE runs SET status='done', finished_at=?, message=? WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), msg, rid))
        con.commit()
        return {"status": "done", "message": msg, "rows": 0 if levels is None else len(levels)}
    except Exception as e:  # noqa: BLE001
        con.execute("UPDATE runs SET status='error', finished_at=?, message=? WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), f"{type(e).__name__}: {e}", rid))
        con.commit()
        raise
