"""CHP 500 指数回测 / 绩效统计。

读取 outputs/index.csv，计算价格指数与全收益指数的年化收益、年化波动、
最大回撤、夏普等，并输出。

用法: python scripts/backtest.py [--out-dir outputs]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def metrics(series: pd.Series, rf: float = 0.0) -> dict:
    ret = series.pct_change().dropna()
    n = len(ret)
    if n < 2:
        return {}
    ann_ret = (series.iloc[-1] / series.iloc[0]) ** (252 / n) - 1
    ann_vol = ret.std() * (252 ** 0.5)
    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else float("nan")
    peak = series.cummax()
    mdd = (series / peak - 1).min()
    return {
        "总收益": f"{(series.iloc[-1] / series.iloc[0] - 1):.2%}",
        "年化收益": f"{ann_ret:.2%}",
        "年化波动": f"{ann_vol:.2%}",
        "夏普(无风险=0)": f"{sharpe:.2f}",
        "最大回撤": f"{mdd:.2%}",
        "交易日数": n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent.parent / "outputs"))
    args = ap.parse_args()
    idx = pd.read_csv(Path(args.out_dir) / "index.csv")
    idx["date"] = pd.to_datetime(idx["date"])

    print("== CHP 500 指数绩效（基于 outputs/index.csv）==")
    for col in ["price_index", "total_return"]:
        m = metrics(idx[col])
        print(f"\n[{col}]")
        for k, v in m.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
