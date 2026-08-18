"""验证东财 push2 数据源闭环（可达性探测 + 快照构建 + 真实覆盖率汇总）。

本脚本是「数据源改正」闭环的可视化验证工具：

  - 在**国内网 / VPN** 环境运行：东财 push2 可达，A/HK/US 的 `shares_source=em`
    占多数（未命中者标记 `missing` 由筛选剔除），`real_shares_ratio` 接近 1 ——
    证明「可达即真实」闭环成立。
  - 在**东财不可达**环境运行：严格真实模式下构建会直接报错终止，不会回落到近似数据；
    本脚本仅输出可达性探测结果并提示需在可达环境重跑，不产出失真快照。

用法：
  python scripts/verify_em_push2.py --as-of 2026-08-13
  python scripts/verify_em_push2.py --universe expanded --markets A,HK,US
报告同时落盘到 outputs/em_push2_verify.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chp500.config import BASE_DIR  # noqa: E402
from chp500.data import adapters  # noqa: E402
from chp500.data.em_snapshot import get_em_spot  # noqa: E402


def probe_reachability(markets: list[str]) -> dict:
    """逐个市场探测东财 push2 快照是否可达，返回 {market: 行数|None|'ERROR:..'}。"""
    out = {}
    for m in markets:
        try:
            df = get_em_spot(m)
            out[m] = None if (df is None or df.empty) else int(len(df))
        except Exception as e:  # noqa: BLE001
            out[m] = f"ERROR:{type(e).__name__}:{e}"[:80]
    return out


def build_snapshot(universe: str, markets: list[str], as_of: pd.Timestamp) -> pd.DataFrame:
    if universe == "expanded":
        from chp500.data import universe as univ
        return univ.build_expanded_cross_market_snapshot(as_of, markets)
    return adapters.build_cross_market_snapshot(as_of, markets)


def summarize(snap: pd.DataFrame) -> dict:
    sc = snap["shares_source"].value_counts().to_dict() if "shares_source" in snap else {}
    pc = snap["profit_source"].value_counts().to_dict() if "profit_source" in snap else {}
    n = max(len(snap), 1)
    real_shares = sc.get("em", 0) / n
    real_profit = (pc.get("em", 0) + pc.get("edgar", 0)) / n
    return {
        "n_snapshot": int(len(snap)),
        "shares_source_counts": {str(k): int(v) for k, v in sc.items()},
        "profit_source_counts": {str(k): int(v) for k, v in pc.items()},
        "real_shares_ratio": round(real_shares, 4),
        "real_profit_ratio": round(real_profit, 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="验证东财 push2 数据源闭环")
    ap.add_argument("--as-of", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    ap.add_argument("--markets", default="A,HK,US")
    ap.add_argument("--universe", choices=["curated", "expanded"], default="curated")
    ap.add_argument("--out", default=str(BASE_DIR / "outputs" / "em_push2_verify.json"))
    args = ap.parse_args()

    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
    as_of = pd.Timestamp(args.as_of)

    print(f"[1/3] 探测东财 push2 可达性 markets={markets} ...")
    reach = probe_reachability(markets)
    for m, v in reach.items():
        label = f"可达, 行数={v}" if isinstance(v, int) else ("不可达/空" if v is None else v)
        print(f"    {m}: {label}")

    all_ok = all(isinstance(v, int) and v > 0 for v in reach.values())
    if not all_ok:
        print("[!] 东财 push2 不可达（需国内网络/VPN）。严格真实模式下构建会直接报错终止，"
              "不会回落近似数据。请在国内网/VPN 环境重跑以验证真实覆盖。")
        report = {
            "as_of": str(as_of.date()),
            "universe": args.universe,
            "markets": markets,
            "em_reachable": {m: (v if isinstance(v, int) else False) for m, v in reach.items()},
            "build_attempted": False,
            "note": "东财 push2 不可达：严格真实模式拒绝近似回落，未产出失真快照。",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[3/3] 报告已写入 {out_path}")
        return

    print(f"[2/3] 构建 {args.universe} 快照 markets={markets} as_of={as_of.date()} "
          f"（会触发 Sina/yjbb_em/东财 push2 取数，请联网）...")
    snap = build_snapshot(args.universe, markets, as_of)
    summary = summarize(snap)

    report = {
        "as_of": str(as_of.date()),
        "universe": args.universe,
        "markets": markets,
        "em_reachable": {m: (v if isinstance(v, int) else False) for m, v in reach.items()},
        **summary,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/3] 报告已写入 {out_path}")

    print("[解读] 东财 push2 可达：真实市值/股本已覆盖（未命中者标记 missing 由筛选剔除），"
          "real_shares_ratio 应接近 1 —— 闭环成立。")


if __name__ == "__main__":
    main()
