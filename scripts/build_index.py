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
from chp500.data import fx as fxmod  # noqa: E402
from chp500.filter import screens  # noqa: E402
from chp500.sector import classifier  # noqa: E402
from chp500.weight import calculator  # noqa: E402
from chp500.committee import review  # noqa: E402
from chp500.index import series as idx  # noqa: E402


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


def run(as_of, mode, out_dir: Path, markets=None, universe="curated"):
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

    # 6) 指数序列（基于成分各自主上市地历史行情 + 主上市地自由流通股，跨市场按汇率折算 CNY）
    start = (as_of - pd.Timedelta(days=365)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    fx_period = fxmod.fetch_fx_history(["USD", "HKD"], start, end)

    cny_prices = {}  # entity_id -> 每日 CNY 每股价格序列

    # 按市场批量并发抓取历史行情（已按 code 全量缓存，重跑命中缓存）
    a_codes = constituents.loc[constituents["market"] == "A", "code"].tolist()
    hk_codes = constituents.loc[constituents["market"] == "HK", "code"].tolist()
    us_codes = constituents.loc[constituents["market"] == "US", "code"].tolist()
    a_prices, _ = adapters.fetch_hist(a_codes, start, end) if a_codes else (pd.DataFrame(), pd.DataFrame())
    hk_prices, _ = adapters.fetch_hk_us_hist(hk_codes, "HK", start, end) if hk_codes else (pd.DataFrame(), pd.DataFrame())
    us_prices, _ = adapters.fetch_hk_us_hist(us_codes, "US", start, end) if us_codes else (pd.DataFrame(), pd.DataFrame())

    for _, row in constituents.iterrows():
        eid, mkt, code, curr = row["entity_id"], row["market"], row["code"], row["curr"]
        try:
            if mkt == "A":
                if code not in a_prices.columns:
                    continue
                ser = a_prices[code].copy()
            else:
                src = hk_prices if mkt == "HK" else us_prices
                if code not in src.columns:
                    continue
                ser = src[code].copy()
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


def main():
    ap = argparse.ArgumentParser(description="CHP 500 指数编制")
    ap.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"), help="再平衡/评估日")
    ap.add_argument("--mode", choices=["demo", "live"], default="demo")
    ap.add_argument("--markets", default=",".join(CONFIG.get("markets", ["A", "HK", "US"])),
                    help="参与市场，逗号分隔，如 A,HK,US")
    ap.add_argument("--out-dir", default=str(BASE_DIR / "outputs"))
    ap.add_argument("--universe", choices=["curated", "expanded"], default="curated",
                    help="curated=精选参考集(~50)；expanded=全量 A 股(真实名/价/利+近似股本)推向~500")
    args = ap.parse_args()
    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
    run(args.as_of, args.mode, Path(args.out_dir), markets, args.universe)


if __name__ == "__main__":
    main()
