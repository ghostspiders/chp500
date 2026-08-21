"""对比基准数据（v1 仅入库，不做归一化对比渲染）。

数据源：akshare（与项目其余行情一致）。先支持沪深300（sh000300），
后续可在 BENCH_REGISTRY 增加中证500/标普500/恒生等。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from ..index.persistent import open_db

# bench_id -> akshare 指数符号（上证/深证指数前缀 sh/sz）
BENCH_REGISTRY: dict[str, str] = {
    "csi300": "sh000300",
    # "csi500": "sh000905",
    # "hsi":    "hsI",   # 恒生（akshare 另行支持）
    # "sp500":  "spx",   # 标普500（经 ETF 或指数接口）
}


def _import_akshare():
    try:
        import akshare as ak  # noqa: F401
        return ak
    except ImportError:
        raise RuntimeError("未安装 akshare，无法抓取基准数据；请先 `pip install akshare`。")


def fetch_benchmark(bench_id: str, start: str, end: str) -> pd.DataFrame:
    """抓取基准日线收盘价。返回 DataFrame[date(datetime), close(float)]。"""
    if bench_id not in BENCH_REGISTRY:
        raise ValueError(f"未知基准 {bench_id!r}；可选：{list(BENCH_REGISTRY)}")
    ak = _import_akshare()
    symbol = BENCH_REGISTRY[bench_id]
    df = ak.stock_zh_index_daily(symbol=symbol)
    if df is None or df.empty or "date" not in df.columns or "close" not in df.columns:
        raise RuntimeError(f"基准 {bench_id}({symbol}) 抓取为空。")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
    return df[["date", "close"]].sort_values("date").reset_index(drop=True)


def upsert_benchmark(con: sqlite3.Connection, bench_id: str, df: pd.DataFrame) -> int:
    """将基准收盘价写入 benchmarks 表（按 (bench_id, date) 幂等）。返回写入条数。"""
    recs = [(bench_id, d.strftime("%Y-%m-%d"), float(c))
            for d, c in zip(df["date"], df["close"]) if pd.notna(c)]
    con.executemany(
        "INSERT OR REPLACE INTO benchmarks (bench_id, date, close) VALUES (?,?,?)", recs)
    con.commit()
    return len(recs)


def refresh_benchmark(con, bench_id: str, start: str, end: str) -> int:
    df = fetch_benchmark(bench_id, start, end)
    return upsert_benchmark(con, bench_id, df)


def list_benchmarks(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT DISTINCT bench_id FROM benchmarks ORDER BY bench_id").fetchall()
    return [r[0] for r in rows]


def benchmark_series(con: sqlite3.Connection, bench_id: str) -> pd.DataFrame:
    rows = con.execute(
        "SELECT date, close FROM benchmarks WHERE bench_id = ? ORDER BY date", (bench_id,)
    ).fetchall()
    return pd.DataFrame(rows, columns=["date", "close"])
