"""CHP 500 指数编制主流程（CLI）。

用法:
  python scripts/build_index.py --mode demo --as-of 2026-08-13
  python scripts/build_index.py --mode live   # 生产：需东财市值主机可达

输出（默认 outputs/）:
  constituents.csv   本期成分（含权重、行业、达标诊断）
  index.csv         价格指数 + 全收益指数序列
  meta.json          运行元信息
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chp500.config import CONFIG, BASE_DIR  # noqa: E402
from chp500.data import adapters  # noqa: E402
from chp500.filter import screens  # noqa: E402
from chp500.sector import classifier  # noqa: E402
from chp500.weight import calculator  # noqa: E402
from chp500.committee import review  # noqa: E402
from chp500.index import persistent as pidx  # noqa: E402
from chp500.data import benchmark as benchmod  # noqa: E402


def build_snapshot(as_of, mode, markets, universe="curated"):
    if mode == "demo":
        if universe == "expanded":
            print(f"[snap] demo 扩展宇宙：全 A(真实名/价/利+近似股本)+HK/US 参考（markets={markets}）...")
            from chp500.data import universe as univ
            return univ.build_expanded_cross_market_snapshot(as_of, markets)
        print(f"[snap] demo 模式：跨市场快照（markets={markets}）...")
        return adapters.build_cross_market_snapshot(as_of, markets)
    else:
        # live 生产模式：未实现（数据通路已收敛为腾讯快照 + Sina 行情 + EDGAR，可基于其补齐）
        raise NotImplementedError("live 模式未实现；当前请使用 demo 模式。")


def _clean_price(ser: pd.Series) -> pd.Series:
    """剔除坏点：0 值，以及"单日跳变超 30% 且次日回到跳变前水平(±10%)"的异常尖刺
    （典型为未复权缺口/异常 tick）。真实的持续暴涨暴跌予以保留，不得抹平。
    """
    ser = ser.replace(0, np.nan)
    ret = ser.pct_change()
    jump = ret.abs() > 0.30
    revert = (ser.shift(-1) / ser.shift(1) - 1.0).abs() < 0.10
    spike = jump & revert.fillna(False)
    ser = ser.mask(spike, ser.shift(1))
    return ser.ffill().bfill()


def run(as_of, mode, out_dir: Path, markets=None, universe="curated", kind="rebalance"):
    out_dir.mkdir(parents=True, exist_ok=True)
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")
    as_of = pd.Timestamp(as_of)
    markets = markets or CONFIG.get("markets", ["A", "HK", "US"])

    snapshot = build_snapshot(as_of, mode, markets, universe)
    # 1) 准入筛选 + 按规模选取最多 target_count 只成分
    n_target = int(CONFIG.get("target_count", 500))
    eligible = screens.select_constituents(snapshot, as_of)
    print(f"[screen] 候选池={len(snapshot)} 通过准入后按规模选取={len(eligible)}（目标≤{n_target}）")

    # 2) 行业分类 + 配比（扩展宇宙 A 股行业在此补齐：雪球，仅对入选成分抓取，缓存复用）
    elig = adapters.attach_industry(eligible)
    elig = classifier.add_sector(elig)
    elig = classifier.allocate(elig, CONFIG, weight_col="float_mcap")

    # 3) 权重计算
    weighted = calculator.compute_weights(elig, CONFIG, mcap_col="float_mcap")

    # 4) 委员会复核（非全自动定稿）
    final, summary = review(weighted, CONFIG)
    print(f"[committee] 建议成分={summary['n_recommended']} "
          f"单股超限={summary['n_single_exceed']} 行业超限={summary['n_sector_exceed']}")

    # 5) 输出成分
    cols = ["entity_id", "code", "name", "market", "curr", "sector", "industry",
            "price", "total_shares", "float_shares", "total_mcap", "float_mcap", "iwf",
            "ttm_net_profit", "latest_q_net_profit", "liquidity_ratio",
            "n_listings", "listings", "weight", "single_exceed",
            "shares_source", "profit_source"]
    constituents = final[cols].sort_values("weight", ascending=False).reset_index(drop=True)
    constituents.to_csv(out_dir / "constituents.csv", index=False, encoding="utf-8-sig")
    print(f"[out] 成分已写入 {out_dir / 'constituents.csv'}（{len(constituents)} 只）")

    # 6) 指数序列（连续累积，落 SQLite；固定基期、篮子变动调除数保持连续）
    #    取代原先「每次重算近一年、基准重置」的覆盖式 index.csv。
    db_path = out_dir / "chp500.db"
    con = pidx.open_db(db_path)
    basket_cols = ["entity_id", "code", "name", "market", "curr", "sector", "industry",
                   "price", "total_shares", "float_shares", "float_mcap", "iwf",
                   "ttm_net_profit", "liquidity_ratio", "weight",
                   "shares_source", "profit_source"]
    basket = final[basket_cols].copy()
    index_cfg = CONFIG.get("index") or {}
    inc = index_cfg.get("inception_date") if isinstance(index_cfg, dict) else None
    base_point = float((index_cfg.get("base_point", 1000.0))
                       if isinstance(index_cfg, dict) else 1000.0)
    summary_idx = pidx.update_index(
        con, basket, str(as_of.date()), kind=kind,
        inception_date=inc, base_point=base_point)
    print(f"[out] 指数已更新（{summary_idx['message']}）；库：{db_path}")
    levels = pd.read_sql_query(
        "SELECT date, price_index, total_return FROM index_levels ORDER BY date", con)
    if not levels.empty:
        levels.to_csv(out_dir / "index.csv", index=False, encoding="utf-8-sig")
        index_df = levels
        print(f"[out] 指数序列已写入 {out_dir / 'index.csv'}（{len(levels)} 个交易日）")
    else:
        print("[warn] 历史行情不足，跳过指数序列")
        index_df = None

    # 各市场成分数量与数据源覆盖（用于元信息）
    mkt_counts = constituents.groupby("market").size().to_dict()
    src_shares = constituents["shares_source"].value_counts().to_dict() if "shares_source" in constituents else {}
    src_profit = constituents["profit_source"].value_counts().to_dict() if "profit_source" in constituents else {}
    n_total = max(len(constituents), 1)
    meta = {
        "as_of": str(as_of.date()),
        "mode": mode,
        "markets": markets,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_universe": int(len(snapshot)),
        "n_eligible": int(len(eligible)),
        "n_constituents": int(len(constituents)),
        "constituents_by_market": {str(k): int(v) for k, v in mkt_counts.items()},
        "shares_source_counts": {str(k): int(v) for k, v in src_shares.items()},
        "profit_source_counts": {str(k): int(v) for k, v in src_profit.items()},
        "real_shares_ratio": round(
            src_shares.get("tencent", 0) / n_total, 4
        ),
        "real_profit_ratio": round(
            (src_profit.get("tencent", 0) + src_profit.get("edgar", 0)) / n_total, 4
        ),
        "config": CONFIG,
        "committee_summary": summary,
        "total_return_note": "未接入分红数据，total_return 与 price_index 数值相同",
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] 元信息已写入 {out_dir / 'meta.json'}")
    return constituents, index_df


def run_daily(as_of, out_dir: Path, universe: str = "curated"):
    """仅按当前篮子补点净值（轻量，无需重算快照；要求已至少有一次再平衡）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")
    as_of = pd.Timestamp(as_of)
    con = pidx.open_db(out_dir / "chp500.db")
    basket = pidx.current_basket(con)
    if basket is None:
        raise RuntimeError("尚无再平衡历史，请先跑一次完整构建（--rebalance）。")
    added = pidx.append_daily(con, basket, str(as_of.date()))
    levels = pd.read_sql_query(
        "SELECT date, price_index, total_return FROM index_levels ORDER BY date", con)
    if not levels.empty:
        levels.to_csv(out_dir / "index.csv", index=False, encoding="utf-8-sig")
    n = 0 if added is None else len(added)
    print(f"[daily] 补点完成：新增 {n} 个交易日；库：{out_dir / 'chp500.db'}")


def run_benchmarks(as_of, out_dir: Path, universe: str = "curated"):
    """刷新对比基准序列（v1 仅入库；归一化对比延后）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = pidx.open_db(out_dir / "chp500.db")
    bench_ids = CONFIG.get("benchmarks") or ["csi300"]
    end = as_of or datetime.now().strftime("%Y-%m-%d")
    end = pd.Timestamp(end)
    start = (end - pd.Timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    for b in bench_ids:
        try:
            n = benchmod.refresh_benchmark(con, b, start, end.strftime("%Y-%m-%d"))
            print(f"[bench] {b}: 入库 {n} 条（{start} ~ {end.date()}）")
        except Exception as e:  # noqa: BLE001
            print(f"[bench] {b} 抓取失败：{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description="CHP 500 指数编制（常年运行：再平衡 + 每日补点 + 基准）")
    ap.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"), help="再平衡/评估日")
    ap.add_argument("--mode", choices=["demo", "live"], default="demo")
    ap.add_argument("--markets", default=",".join(CONFIG.get("markets", ["A", "HK", "US"])),
                    help="参与市场，逗号分隔，如 A,HK,US")
    ap.add_argument("--out-dir", default=None,
                    help="输出目录（默认 outputs/<universe>，与 API 一致）")
    ap.add_argument("--universe", choices=["curated", "expanded"], default="curated",
                    help="curated=精选参考集(~50)；expanded=全量 A 股(真实名/价/利+近似股本)推向~500")
    # 运行模式
    ap.add_argument("--rebalance", action="store_true",
                    help="完整再平衡（默认；建库或篮子变动均走此路径）")
    ap.add_argument("--daily", action="store_true",
                    help="仅按当前篮子补点净值（轻量，不重算快照）")
    ap.add_argument("--iwf-refresh", action="store_true",
                    help="周度 IWF/股本刷新（重算快照，调除数保持连续）")
    ap.add_argument("--backfill", action="store_true",
                    help="从 config.index.inception_date 起一次性建库/补历史")
    ap.add_argument("--benchmarks", action="store_true",
                    help="仅刷新对比基准序列（沪深300 等）")
    args = ap.parse_args()
    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
    out_dir = Path(args.out_dir) if args.out_dir else (BASE_DIR / "outputs" / args.universe)

    if args.benchmarks:
        run_benchmarks(args.as_of, out_dir, args.universe)
    elif args.daily:
        run_daily(args.as_of, out_dir, args.universe)
    else:
        kind = "iwf_refresh" if args.iwf_refresh else ("backfill" if args.backfill else "rebalance")
        run(args.as_of, args.mode, out_dir, markets, args.universe, kind=kind)


if __name__ == "__main__":
    main()
