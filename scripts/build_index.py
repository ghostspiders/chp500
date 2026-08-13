"""CN 500 指数编制主流程（CLI）。

用法:
  python scripts/build_index.py --mode demo --as-of 2026-08-13
  python scripts/build_index.py --mode live   # 生产：需东财市值可达 / 配 Tushare

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

from cn500.config import CONFIG, BASE_DIR  # noqa: E402
from cn500.data import adapters  # noqa: E402
from cn500.data import fx as fxmod  # noqa: E402
from cn500.filter import screens  # noqa: E402
from cn500.sector import classifier  # noqa: E402
from cn500.weight import calculator  # noqa: E402
from cn500.committee import review  # noqa: E402
from cn500.index import series as idx  # noqa: E402


def build_snapshot(as_of, mode, markets):
    if mode == "demo":
        print(f"[snap] demo 模式：跨市场快照（markets={markets}）...")
        return adapters.build_cross_market_snapshot(as_of, markets)
    else:
        # 生产模式：东财市值可达时
        print("[snap] live 模式：抓取全 A 快照 ...")
        universe = adapters.fetch_a_universe()
        mcap = adapters.fetch_a_market_cap_em()
        # TODO: 合并 quotes / earnings / liquidity（见 adapters 各函数）
        raise NotImplementedError("live 模式需东财市值主机可达；本环境被墙，请用 demo 或配 Tushare。")


def run(as_of, mode, out_dir: Path, markets=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    as_of = pd.Timestamp(as_of)
    markets = markets or CONFIG.get("markets", ["A", "HK", "US"])

    snapshot = build_snapshot(as_of, mode, markets)
    # 1) 准入筛选
    diag = screens.add_screen_diagnostics(snapshot, as_of)
    eligible = diag[diag["eligible"]].copy()
    print(f"[screen] 候选池={len(snapshot)} 通过准入={len(eligible)}")

    # 2) 行业分类 + 配比（快照已带 sector；若缺失则从 industry 映射）
    elig = eligible
    if "sector" not in elig.columns:
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
            "n_listings", "listings", "weight", "single_exceed"]
    constituents = final[cols].sort_values("weight", ascending=False).reset_index(drop=True)
    constituents.to_csv(out_dir / "constituents.csv", index=False, encoding="utf-8-sig")
    print(f"[out] 成分已写入 {out_dir / 'constituents.csv'}（{len(constituents)} 只）")

    # 6) 指数序列（基于成分各自主上市地历史行情 + 主上市地自由流通股，跨市场按汇率折算 CNY）
    start = (as_of - pd.Timedelta(days=365)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    fx_period = fxmod.fetch_fx_history(["USD", "HKD"], start, end)

    cny_prices = {}  # entity_id -> 每日 CNY 每股价格序列

    def _clean_price(ser: pd.Series) -> pd.Series:
        """剔除坏点：0 值、单日涨跌超 30%（疑似未复权缺口/异常 tick）用前值替代。"""
        ser = ser.replace(0, np.nan)
        ret = ser.pct_change()
        bad = ret.abs() > 0.30
        ser = ser.mask(bad, ser.shift(1))
        return ser.ffill().bfill()

    for _, row in constituents.iterrows():
        eid, mkt, code, curr = row["entity_id"], row["market"], row["code"], row["curr"]
        try:
            if mkt == "A":
                p, _ = adapters.fetch_hist([code], start, end)
                if p.empty or code not in p.columns:
                    continue
                ser = p[code].copy()
            else:
                p, _ = adapters.fetch_hk_us_hist([code], mkt, start, end)
                if p.empty or code not in p.columns:
                    continue
                ser = p[code].copy()
                fxser = fx_period.get(curr, pd.Series(1.0, index=ser.index))
                fxser = fxser.reindex(ser.index).ffill().bfill().fillna(1.0)
                ser = ser * fxser  # 折算 CNY
        except Exception:  # noqa: BLE001
            continue
        ser = _clean_price(ser)
        if ser is None or ser.empty or ser.notna().sum() < 2:
            continue
        cny_prices[eid] = ser

    cny_prices = pd.DataFrame(cny_prices)
    cny_prices = cny_prices.dropna(how="all").sort_index()
    # 跨市场行情源（Sina A/HK/US）偶有缺失交易日，按"个股最新已知价向前填充"对齐，
    # 避免成分在某日缺数被剔出导致指数无谓跳变（指数编制标准做法）。
    cny_prices = cny_prices.ffill()
    fs = constituents.set_index("entity_id")["float_shares"]
    common = [e for e in cny_prices.columns if e in fs.index]
    cny_prices = cny_prices[common]
    fs = fs[common]
    if cny_prices.shape[0] > 1 and not fs.empty:
        index_df = idx.build_series(cny_prices, fs, base_point=1000.0)
        index_df.to_csv(out_dir / "index.csv", encoding="utf-8-sig")
        print(f"[out] 指数序列已写入 {out_dir / 'index.csv'}（{len(index_df)} 个交易日，"
              f"{len(common)} 只成分有行情）")
    else:
        print("[warn] 历史行情不足，跳过指数序列")
        index_df = None

    # 各市场成分数量（用于元信息）
    mkt_counts = constituents.groupby("market").size().to_dict()
    meta = {
        "as_of": str(as_of.date()),
        "mode": mode,
        "markets": markets,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_universe": int(len(snapshot)),
        "n_eligible": int(len(eligible)),
        "n_constituents": int(len(constituents)),
        "constituents_by_market": {str(k): int(v) for k, v in mkt_counts.items()},
        "config": CONFIG,
        "committee_summary": summary,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] 元信息已写入 {out_dir / 'meta.json'}")
    return constituents, index_df


def main():
    ap = argparse.ArgumentParser(description="CN 500 指数编制")
    ap.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"), help="再平衡/评估日")
    ap.add_argument("--mode", choices=["demo", "live"], default="demo")
    ap.add_argument("--markets", default=",".join(CONFIG.get("markets", ["A", "HK", "US"])),
                    help="参与市场，逗号分隔，如 A,HK,US")
    ap.add_argument("--out-dir", default=str(BASE_DIR / "outputs"))
    args = ap.parse_args()
    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
    run(args.as_of, args.mode, Path(args.out_dir), markets)


if __name__ == "__main__":
    main()
