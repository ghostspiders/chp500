"""AkShare 数据适配层。

已验证在本环境可用的接口（探针结果）：
  - stock_info_a_code_name   : A 股代码/名称（可用）
  - stock_zh_a_spot          : Sina 批量现价/成交量（可用，但无市值）
  - stock_zh_a_daily         : Sina A 股日线（东财历史主机被墙，改用 Sina）
  - stock_hk_daily           : Sina 港股日线（可用，港股无东财墙）
  - stock_us_daily           : Sina 美股日线（可用）
  - currency_boc_sina        : 中行历史汇率（USD/HKD -> CNY，可用）
  另见 spot.py（腾讯行情三市场市值/股本/PE 快照，TTM 净利由 总市值/PE(TTM) 推导）、
  xueqiu.py（雪球 A 股行业）、edgar.py（SEC EDGAR 美股净利，免认证）。
  原东财 yjbb_em / 东财港股财报通路已移除（减少数据源；见 README 数据源说明）；
  HK/US 行业由参考表（demo_hk/demo_us.csv）人工核定提供（免费接口无港美行业字段）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import re
import time

import akshare as ak
import numpy as np
import pandas as pd
import requests

from ..config import BASE_DIR, CONFIG
from . import edgar, fx
from .cache import Cache
from .sources import source_url
from .spot import fetch_spot
from .xueqiu import fetch_a_industry
from .merge import merge_entities

DATA_DIR = BASE_DIR / "data"
CACHE = Cache()


# ---------------------------------------------------------------------------
# 基础行情 / 行情
# ---------------------------------------------------------------------------
_A_UNIVERSE_CACHE = DATA_DIR / "a_universe.csv"


def fetch_a_universe() -> pd.DataFrame:
    """A 股全量代码/名称。

    优先走 akshare（SSE+SZSE 实时）。深交所(szse.cn)在本环境偶发不可达，
    此时回退到本地缓存 data/a_universe.csv（实时获取成功后落盘，或已由历史构建产物播种）；
    若连缓存都没有，最后退而求其次只取 SSE 主板，保证构建不完全中断。
    """
    try:
        df = ak.stock_info_a_code_name()
        df = df[["code", "name"]]
        df["code"] = df["code"].astype(str).str.zfill(6)
        df.to_csv(_A_UNIVERSE_CACHE, index=False, encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001
        if _A_UNIVERSE_CACHE.exists():
            print(f"[warn] akshare 获取 A 股宇宙失败，回退本地缓存 data/a_universe.csv：{e}")
            df = pd.read_csv(_A_UNIVERSE_CACHE, dtype={"code": str})
        else:
            try:
                sse = ak.stock_info_sh_name_code(symbol="主板A股")[["code", "name"]]
                sse["code"] = sse["code"].astype(str).str.zfill(6)
                print(f"[warn] 深交所行情源不可达，A 股宇宙回退为 SSE 主板（{len(sse)} 只）：{e}")
                sse["market"] = "A"
                sse["entity_id"] = "A." + sse["code"]
                return sse[["entity_id", "code", "name", "market"]]
            except Exception:
                raise RuntimeError(f"A 股宇宙获取失败且无本地缓存：{e}")
    df["market"] = "A"
    df["entity_id"] = "A." + df["code"].astype(str)
    return df[["entity_id", "code", "name", "market"]]


def fetch_a_quotes_sina() -> pd.DataFrame:
    """A 股批量现价（及成交量）。优先 Sina，失败回退腾讯行情（本环境 Sina 偶发被拦截）。

    下游仅消费 price 列；volume/amount 在回退时置空。
    """
    last_err = None
    for _ in range(3):
        try:
            return _fetch_a_quotes_sina_impl()
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}:{e}"[:120]
            time.sleep(3)
    print(f"[warn] Sina 行情获取失败（{last_err}），回退腾讯行情")
    return _fetch_a_quotes_tencent()


def _fetch_a_quotes_sina_impl() -> pd.DataFrame:
    df = ak.stock_zh_a_spot()
    df = df.rename(columns={"代码": "code", "名称": "name", "最新价": "price",
                            "成交量": "volume", "成交额": "amount"})
    # Sina 代码带交易所前缀（sh/sz/bj），提取 6 位数字
    df["code"] = df["code"].astype(str).str.extract(r"(\d{6})")[0]
    df = df[["code", "name", "price", "volume", "amount"]]
    # 校验：Sina 偶发返回残缺/异常（如仅北交所部分、或 HTML 误解析），
    # 视为不可用，触发上层回退腾讯行情。
    if len(df) < 1000 or df["price"].isna().all():
        raise ValueError(f"Sina 行情记录数异常/无价格: {len(df)} 行")
    return df


def _fetch_a_quotes_tencent() -> pd.DataFrame:
    """腾讯 qt 兜底：取全 A 现价（本环境比 Sina 稳定）。仅保证 code/name/price。"""
    info = ak.stock_info_a_code_name()
    codes = info["code"].astype(str).tolist()
    tq = [("sh" + c) if c[:1] == "6" else ("sz" + c) for c in codes]
    qt_base = source_url("tencent_spot", "https://qt.gtimg.cn/q=")
    rows = []
    for i in range(0, len(tq), 150):
        try:
            r = requests.get(qt_base + ",".join(tq[i:i + 150]), timeout=25)
            txt = r.content.decode("gbk", "ignore")
        except Exception:  # noqa: BLE001
            continue
        for line in txt.split(";"):
            m = re.match(r'v_(\w+)="(.*)"', line.strip())
            if not m:
                continue
            f = m.group(2).split("~")
            if len(f) <= 3:
                continue
            try:
                price = float(f[3])
            except (ValueError, IndexError):
                continue
            if price <= 0:
                continue
            rows.append({"code": re.sub(r"[^0-9]", "", m.group(1)),
                         "name": f[1], "price": price,
                         "volume": float("nan"), "amount": float("nan")})
    return pd.DataFrame(rows, columns=["code", "name", "price", "volume", "amount"])


# ---------------------------------------------------------------------------
def _sina_a_daily(code: str) -> pd.DataFrame | None:
    """抓取单只 A 股全量日线（Sina，前复权）。返回 date/open/high/low/close/volume。

    Sina 偶发瞬时失败/限流，做 3 次重试；失败返回 None。
    """
    prefix = "sh" if str(code)[0] == "6" else "sz"
    last_err = None
    for _ in range(3):
        try:
            df = ak.stock_zh_a_daily(symbol=prefix + str(code), adjust="qfq")
            if df is None or df.empty or "date" not in df.columns:
                last_err = "empty/no-date"
                continue
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            return df.reset_index(drop=True)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}:{e}"[:80]
            continue
    print(f"  [warn] A {code} 行情获取失败（{last_err}），跳过")
    return None


def _cached_a_daily(code: str) -> pd.DataFrame | None:
    return CACHE.get_or_fetch(f"a_daily_{code}", _sina_a_daily, str(code))


def fetch_hist(codes, start: str, end: str, workers: int = 12) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A 股历史日线（Sina daily，按 code 全量缓存复用，并发抓取）。

    返回 (价格表, 成交量表)：索引=日期，列=code。
    """
    start_s = pd.Timestamp(start)
    end_s = pd.Timestamp(end)
    codes = [str(c) for c in codes]
    price_parts, vol_parts = [], []

    def _job(code: str):
        return code, _cached_a_daily(code)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, c): c for c in codes}
        for fut in as_completed(futs):
            code, df = fut.result()
            if df is None or df.empty:
                continue
            m = (df["date"] >= start_s) & (df["date"] <= end_s)
            sub = df.loc[m]
            if sub.empty:
                continue
            price_parts.append(sub.set_index("date")["close"].rename(code))
            vol_parts.append(sub.set_index("date")["volume"].rename(code))
    prices = pd.concat(price_parts, axis=1) if price_parts else pd.DataFrame()
    volumes = pd.concat(vol_parts, axis=1) if vol_parts else pd.DataFrame()
    return prices, volumes


def compute_liquidity(volumes: pd.DataFrame, float_shares: pd.Series, months: int = 6) -> pd.Series:
    """流动性比率 = 近 months 个月累计成交量(股) / 自由流通股数。"""
    window = max(1, int(months * 21))
    recent = volumes.tail(window)
    cum_vol = recent.sum(axis=0)
    out = cum_vol / float_shares.reindex(cum_vol.index)
    return out


# ---------------------------------------------------------------------------
# 港股 / 美股（Sina daily，本环境可用）
# ---------------------------------------------------------------------------
def _sina_hk_us_daily(code: str, market: str) -> pd.DataFrame | None:
    """抓取单只港股/美股全量日线（Sina）。返回 date/open/high/low/close/volume。

    Sina 偶发返回空表或字段缺失（限流），做重试与字段校验，失败返回 None。
    """
    if market == "HK":
        func = ak.stock_hk_daily
    else:
        func = ak.stock_us_daily
    last_err = None
    for attempt in range(3):
        try:
            df = func(symbol=str(code), adjust="qfq")
            if df is None or df.empty or "date" not in df.columns:
                last_err = "empty/no-date"
                continue
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            if df.empty:
                last_err = "no-valid-date"
                continue
            return df.sort_values("date").reset_index(drop=True)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}:{e}"[:80]
            continue
    print(f"  [warn] {market} {code} 行情获取失败（{last_err}），跳过")
    return None


def _cached_hk_us_daily(code: str, market: str) -> pd.DataFrame | None:
    key = f"{market}_daily_{code}"
    return CACHE.get_or_fetch(key, _sina_hk_us_daily, code, market)


def fetch_hk_us_price(codes, market: str, as_of: datetime) -> pd.Series:
    """取 as_of 当日或之前最近收盘价的本地货币价格。返回 code->price。"""
    as_of = pd.Timestamp(as_of)
    out = {}
    for code in codes:
        df = _cached_hk_us_daily(str(code), market)
        if df is None or df.empty:
            continue
        sub = df[df["date"] <= as_of]
        if sub.empty:
            continue
        out[str(code)] = float(sub.iloc[-1]["close"])
    return pd.Series(out, name="price")


def fetch_hk_us_hist(codes, market: str, start: str, end: str, workers: int = 12) -> tuple[pd.DataFrame, pd.DataFrame]:
    """港股/美股历史日线。返回 (价格表, 成交量表)，索引=日期，列=code，成交量单位=股。"""
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    codes = [str(c) for c in codes]
    price_parts, vol_parts = [], []

    def _job(code: str):
        return code, _cached_hk_us_daily(code, market)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_job, c): c for c in codes}
        for fut in as_completed(futs):
            code, df = fut.result()
            if df is None or df.empty:
                continue
            d = df[(df["date"] >= start) & (df["date"] <= end)]
            if d.empty:
                continue
            price_parts.append(d.set_index("date")["close"].rename(str(code)))
            vol_parts.append(d.set_index("date")["volume"].rename(str(code)))
    prices = pd.concat(price_parts, axis=1) if price_parts else pd.DataFrame()
    volumes = pd.concat(vol_parts, axis=1) if vol_parts else pd.DataFrame()
    return prices, volumes


# ---------------------------------------------------------------------------
def build_a_listing_snapshot(as_of: datetime | None = None) -> pd.DataFrame:
    """A 股 listing 级快照（严格真实模式）。

    - 市值/股本/IWF/TTM 净利：腾讯行情快照，不可达直接报错终止；
      TTM 净利 = 总市值/PE(TTM) 推导（实测与东财业绩真值偏差<0.1%，
      亏损股 PE 为负、符号可靠）；
    - 现价：Sina（与腾讯快照价一致，作为基准），腾讯将覆盖为权威价；
    - 行业：雪球 affiliate_industry（无静态兜底），取不到真实行业者剔除；
    - 流动性：Sina daily 成交量 / 腾讯真实自由流通股。
    不再有任何近似回落（reference/synthetic/static）。
    """
    from ..sector.classifier import map_to_sector

    as_of = pd.Timestamp(as_of or datetime.now())
    ref = pd.read_csv(DATA_DIR / "demo_universe.csv", dtype={"code": str})
    ref["code"] = ref["code"].str.zfill(6)

    quotes = fetch_a_quotes_sina()
    qmap = quotes.set_index("code")[["price"]]

    ind = fetch_a_industry(ref["code"].astype(str).tolist())
    if not ind:
        raise RuntimeError("雪球行业源不可用：无法获取 A 股真实行业，构建已终止。")
    ref = ref[ref["code"].isin(ind)].copy()

    start = (as_of - timedelta(days=200)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    _, volumes = fetch_hist(ref["code"].tolist(), start, end)

    spot = fetch_spot("A", ref["code"].astype(str).tolist())
    if spot is None or spot.empty:
        raise RuntimeError("腾讯行情（A 股快照）不可达：无法获取真实市值/股本，构建已终止。")

    out = pd.DataFrame({
        "entity_id": ref["entity_id"].values,
        "code": ref["code"].values,
        "name": ref["name"].values,
        "market": "A",
        "curr": "CNY",
        "is_st": ref["name"].str.contains("ST", case=False, na=False).values,
        "is_china": True,
        "listing_date": pd.to_datetime(ref["listing_date"]).values,
    })
    out["price"] = out["code"].map(qmap["price"])

    # 腾讯行情真实市值/股本 + PE 推导 TTM 净利（严格：不可达已在上游报错，绝不回落参考/合成）
    out = apply_spot_shares(out, spot, fxr=1.0)

    out["industry"] = out["code"].map(ind)
    out["sector"] = out["industry"].map(map_to_sector)

    float_shares_series = pd.Series(out["float_shares"].values, index=out["code"].values)
    liq = compute_liquidity(volumes, float_shares_series)
    out["liquidity_ratio"] = out["code"].map(liq)
    return out

def apply_spot_shares(out: pd.DataFrame, spot: pd.DataFrame | None, fxr: float) -> pd.DataFrame:
    """用腾讯行情真实快照覆盖价格/股本/市值/TTM 净利（本币与 CNY）。

    - shares_source=tencent；TTM 净利 = 总市值(CNY)/PE(TTM)，profit_source=tencent：
      PE>0 盈利、PE<0 亏损（符号可靠，量级实测偏差<0.1%）、PE 缺失置 NaN 由盈利筛选剔除；
      美股不在此推导（由 SEC EDGAR 权威覆盖，保持 profit_source=missing）。
    - 严格模式：spot 不可达（None/空）直接抛出 RuntimeError，绝不降级到参考/合成/静态近似值。
      个股未命中（停牌/未收录）标记 shares_source="missing" 并将市值/股本/净利置 NaN，
      由下游筛选剔除（不做近似回填）。
    """
    if spot is None or spot.empty:
        raise RuntimeError(
            "腾讯行情快照不可达：无法获取真实市值/股本，已终止以避免使用近似数据。"
        )
    out = out.copy()
    # 先确保所有目标列存在（.loc 对缺失列赋值在部分 pandas 版本不可靠）
    _need_cols = ["total_shares", "float_shares", "iwf",
                  "total_mcap_local", "float_mcap_local",
                  "total_mcap", "float_mcap",
                  "ttm_net_profit", "latest_q_net_profit"]
    for c in _need_cols:
        if c not in out.columns:
            out[c] = np.nan
    m = spot.set_index("code")
    hit = out["code"].isin(m.index)
    idx = out.index[hit]
    if not len(idx):
        raise RuntimeError("腾讯行情快照未覆盖任何成分代码，无法获取真实市值/股本。")
    price = out.loc[idx, "code"].map(m["price"]).astype(float)
    tm = out.loc[idx, "code"].map(m["total_mcap_local"]).astype(float)
    fm = out.loc[idx, "code"].map(m["float_mcap_local"]).astype(float)
    pe = (out.loc[idx, "code"].map(m["pe_ttm"]).astype(float)
          if "pe_ttm" in m.columns else pd.Series(np.nan, index=idx))
    valid = price > 0
    idx = idx[valid]
    price, tm, fm, pe = price[valid], tm[valid], fm[valid], pe[valid]
    out.loc[idx, "price"] = price
    out.loc[idx, "total_shares"] = tm / price
    out.loc[idx, "float_shares"] = fm / price
    out.loc[idx, "iwf"] = fm / tm
    out.loc[idx, "total_mcap_local"] = tm
    out.loc[idx, "float_mcap_local"] = fm
    out.loc[idx, "total_mcap"] = tm * fxr  # 折算 CNY
    out.loc[idx, "float_mcap"] = fm * fxr
    out.loc[idx, "shares_source"] = "tencent"
    # TTM 净利（CNY）= 总市值 / PE(TTM)；PE=0/缺失 -> NaN；美股留给 EDGAR
    if "market" in out.columns:
        use_pe = out.loc[idx, "market"] != "US"
    else:
        use_pe = pd.Series(True, index=idx)
    ttm = (tm * fxr) / pe.replace(0.0, np.nan)
    out.loc[idx, "ttm_net_profit"] = ttm.where(use_pe)
    # 单季净利随东财业绩源移除不再可得，保留列以维持 schema（盈利筛选已改为仅 TTM>0）
    out.loc[idx, "latest_q_net_profit"] = np.nan
    out.loc[idx, "profit_source"] = np.where(use_pe, "tencent", "missing")
    # 未命中个股：无真实市值/股本，置缺失并标记，下游筛选自然剔除（不回填近似）。
    uncovered = out.index.difference(idx)
    out.loc[uncovered, _need_cols] = np.nan
    out.loc[uncovered, "shares_source"] = "missing"
    out.loc[uncovered, "profit_source"] = "missing"
    return out


def apply_real_earnings(out: pd.DataFrame, market: str, usd_cny: float) -> pd.DataFrame:
    """美股净利：SEC EDGAR 真实覆盖 TTM/最新单季。

    HK/A 的 TTM 净利已由 apply_spot_shares 以腾讯 PE 推导，本函数仅处理 US。
    严格模式：失败个股标记 profit_source="missing" 并将净利润置 NaN，由下游盈利筛选剔除；
    不再回落静态近似值。若整批均无真实财务（EDGAR 整体不可用），抛出 RuntimeError。
    """
    if market != "US":
        return out
    out = out.copy()
    out["ttm_net_profit"] = np.nan
    out["latest_q_net_profit"] = np.nan
    out["profit_source"] = "missing"
    got_real = False
    for i, r in out.iterrows():
        try:
            res = edgar.fetch_us_net_income(r["code"], usd_cny=usd_cny)
            if res is not None:
                out.loc[i, "ttm_net_profit"] = res["ttm"]
                out.loc[i, "latest_q_net_profit"] = res["latest_q"]
                out.loc[i, "profit_source"] = "edgar"
                got_real = True
        except Exception:  # noqa: BLE001
            continue
    if not got_real:
        raise RuntimeError(
            "US 真实财务源（SEC EDGAR）整体不可用：无法获取真实净利润，构建已终止。"
        )
    return out


def build_hk_us_candidates(market: str) -> pd.DataFrame:
    """港股/美股候选池读取（可插拔）。

    默认读取人工核定的参考表（demo_hk.csv / demo_us.csv）。若同目录下
    `generated/{hk,us}_candidates.csv` 存在，则合并并按 code 去重（人工表优先），
    便于后续用可达数据源（如 akshare）离线生成更全的候选池，而无需改动此处。
    """
    fname = "demo_hk.csv" if market == "HK" else "demo_us.csv"
    ref = pd.read_csv(DATA_DIR / fname, dtype={"code": str})
    gen = DATA_DIR / "generated" / f"{(market or '').lower()}_candidates.csv"
    if gen.exists():
        extra = pd.read_csv(gen, dtype={"code": str})
        ref = pd.concat([ref, extra], ignore_index=True).drop_duplicates(subset=["code"], keep="first")
    return ref


def build_hk_us_listing_snapshot(as_of: datetime | None, market: str, fx_table: pd.DataFrame) -> pd.DataFrame:
    """港股/美股 listing 级快照（严格真实模式）。

    - 行业：参考表人工核定（demo_hk/demo_us 的 industry 列；港美免费行情接口
      无行业字段，见 README 已知限制；展示字段，不参与权重/筛选）；
    - 上市日：真实 Sina 全量日线首日推导（无静态兜底）；
    - 股本/市值：腾讯行情真实快照，不可达直接报错终止；
    - 财务：HK=腾讯快照 PE 推导 TTM；US=SEC EDGAR；缺失标记 missing 由筛选剔除；
    - 现价/成交量：Sina daily（价格与腾讯快照价一致）。
    不再有任何近似回落（reference/synthetic/static）。
    """
    from ..sector.classifier import map_to_sector, classify_hk_us_sector

    as_of = pd.Timestamp(as_of or datetime.now())
    fname = "demo_hk.csv" if market == "HK" else "demo_us.csv"
    curr = "HKD" if market == "HK" else "USD"
    ref = build_hk_us_candidates(market)

    codes = ref["code"].astype(str).tolist()
    prices = fetch_hk_us_price(codes, market, as_of)

    start = (as_of - timedelta(days=200)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    _, volumes = fetch_hk_us_hist(codes, market, start, end)

    fxr = fx.fx_rate_on(fx_table, as_of, curr)
    spot = fetch_spot(market, ref["code"].astype(str).tolist())
    if spot is None or spot.empty:
        raise RuntimeError(
            f"腾讯行情（{market} 快照）不可达：无法获取真实市值/股本，构建已终止。"
        )

    out = pd.DataFrame({
        "entity_id": ref["entity_id"].values,
        "code": ref["code"].astype(str).values,
        "name": ref["name"].values,
        "market": market,
        "curr": curr,
        "is_st": ref["name"].str.contains("ST", case=False, na=False).values,
        "is_china": True,
    })
    out["price"] = out["code"].map(prices)

    # 腾讯真实股本/市值 + PE 推导 TTM 净利（严格：不可达已在上游报错）
    out = apply_spot_shares(out, spot, fxr)
    # 真实财务覆盖：US=SEC EDGAR（HK 已由腾讯 PE 推导）；缺失标记 missing，不回落静态
    out = apply_real_earnings(out, market, usd_cny=fx.fx_rate_on(fx_table, as_of, "USD"))

    # 行业：参考表人工核定（最高优先级）+ GICS 映射；缺失时退化为对名称做关键词归类
    out["industry"] = out["code"].map(ref.set_index("code")["industry"])
    out["sector"] = out.apply(lambda r: classify_hk_us_sector(r.get("name"), r.get("industry")), axis=1)
    # 真实上市日（Sina 全量日线首日推导）
    out["listing_date"] = out["code"].map(lambda c: _first_listed_date(c, market))

    float_shares_series = pd.Series(out["float_shares"].values, index=out["code"].values)
    liq = compute_liquidity(volumes, float_shares_series)
    out["liquidity_ratio"] = out["code"].map(liq)
    return out


def attach_industry(df: pd.DataFrame) -> pd.DataFrame:
    """为缺少行业的成分（扩展宇宙 A 股，选样后）补齐雪球真实行业。

    全量 A 股逐个抓雪球不现实，故扩展宇宙在快照阶段不取行业，入选成分后在此补齐
    （结果缓存，重跑秒级）。仅处理 market=A 且 industry 为空的行；抓取失败者保持空，
    板块映射时归入"其他"。HK/US 行业已在快照构建时取得。
    """
    out = df.copy()
    if "industry" not in out.columns:
        out["industry"] = None
    need = out["industry"].isna() | (out["industry"].astype(str).str.strip() == "")
    if "market" in out.columns:
        need &= out["market"] == "A"
    codes = out.loc[need, "code"].astype(str).tolist()
    if codes:
        ind = fetch_a_industry(codes)
        out.loc[need, "industry"] = out.loc[need, "code"].astype(str).map(ind)
    return out


def _first_listed_date(code: str, market: str) -> pd.Timestamp:
    """真实上市日：由 Sina 全量日线首日推导（缓存命中，无需另抓历史）。"""
    df = _cached_hk_us_daily(str(code), market)
    if df is None or df.empty:
        return pd.NaT
    s = df["date"].dropna()
    return s.min() if not s.empty else pd.NaT


def build_cross_market_snapshot(as_of: datetime | None = None, markets: list[str] | None = None) -> pd.DataFrame:
    """跨市场合并快照（A + 港股中资 + 美股中概），返回 entity 级快照（CNY 市值）。

    - 各市场 listing 级快照 → 按 entity_id 合并为主上市地计价的 entity 快照。
    - 港股/美股市值按 as_of 中行汇率折算为 CNY。
    """
    as_of = pd.Timestamp(as_of or datetime.now())
    markets = markets or CONFIG.get("markets", ["A", "HK", "US"])
    fx_start = (as_of - timedelta(days=400)).strftime("%Y%m%d")
    fx_end = as_of.strftime("%Y%m%d")
    fx_table = fx.fetch_fx_history(["USD", "HKD"], fx_start, fx_end)

    parts = []
    if "A" in markets:
        parts.append(build_a_listing_snapshot(as_of))
    if "HK" in markets:
        parts.append(build_hk_us_listing_snapshot(as_of, "HK", fx_table))
    if "US" in markets:
        parts.append(build_hk_us_listing_snapshot(as_of, "US", fx_table))
    listing = pd.concat(parts, ignore_index=True)
    return merge_entities(listing)


# 向后兼容：demo 模式默认走跨市场快照
def build_demo_snapshot(as_of: datetime | None = None) -> pd.DataFrame:
    """[兼容] 默认按 config.markets 构建跨市场演示快照。"""
    return build_cross_market_snapshot(as_of, CONFIG.get("markets", ["A", "HK", "US"]))
