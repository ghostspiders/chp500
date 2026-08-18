"""AkShare 数据适配层。

已验证在本环境可用的接口（探针结果）：
  - stock_info_a_code_name   : A 股代码/名称（可用）
  - stock_zh_a_spot          : Sina 批量现价/成交量（可用，但无市值）
  - stock_yjbb_em(date=...)  : 批量业绩报表（净利润 + 所处行业，可用）
  - stock_zh_a_daily         : Sina A 股日线（东财历史主机被墙，改用 Sina）
  - stock_hk_daily           : Sina 港股日线（可用，港股无东财墙）
  - stock_us_daily           : Sina 美股日线（可用）
  - currency_boc_sina        : 中行历史汇率（USD/HKD -> CNY，可用）
  - stock_financial_hk_report_em : 港股三大报表（需国内网络/VPN；断线自动降级）
  另见 em_snapshot.py（东财 push2 三市场市值/股本快照，需国内网络/VPN）
  与 edgar.py（SEC EDGAR 美股净利，免认证）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

from ..config import BASE_DIR, CONFIG
from . import edgar, fx
from .cache import Cache
from .em_snapshot import get_em_spot
from .merge import merge_entities
from .ttm_periods import compute_ttm_from_periods

DATA_DIR = BASE_DIR / "data"
CACHE = Cache()


# ---------------------------------------------------------------------------
# 基础行情 / 行情
# ---------------------------------------------------------------------------
def fetch_a_universe() -> pd.DataFrame:
    """A 股全量代码/名称。"""
    df = ak.stock_info_a_code_name()
    df = df.rename(columns={"code": "code", "name": "name"})
    df["market"] = "A"
    df["entity_id"] = "A." + df["code"].astype(str)
    return df[["entity_id", "code", "name", "market"]]


def fetch_a_quotes_sina() -> pd.DataFrame:
    """Sina 批量现价与成交量（无市值）。代码列为 6 位字符串。"""
    df = ak.stock_zh_a_spot()
    df = df.rename(columns={"代码": "code", "名称": "name", "最新价": "price",
                            "成交量": "volume", "成交额": "amount"})
    # Sina 代码带交易所前缀（sh/sz/bj），提取 6 位数字
    df["code"] = df["code"].astype(str).str.extract(r"(\d{6})")[0]
    return df[["code", "name", "price", "volume", "amount"]]


# ---------------------------------------------------------------------------
# 业绩 / 行业（批量）
# ---------------------------------------------------------------------------
def _quarter_ends(upto: datetime, n: int = 9) -> list[str]:
    """生成截至 upto 的最近 n 个报告期（0331/0630/0930/1231）。"""
    ends = []
    d = pd.Timestamp(upto)
    q_end_months = [(3, 31), (6, 30), (9, 30), (12, 31)]
    cur = None
    for y in range(d.year - 1, d.year + 1):
        for m, day in q_end_months:
            cand = pd.Timestamp(y, m, day)
            if cand <= d:
                cur = cand
    q = cur
    while len(ends) < n:
        ends.append(q.strftime("%Y%m%d"))
        if q.month == 3:
            q = pd.Timestamp(q.year - 1, 12, 31)
        else:
            q = pd.Timestamp(q.year, q.month - 3, [31, 31, 30, 30][q.month // 3 - 1])
    return ends  # 由近到远


def fetch_a_earnings(as_of: datetime | None = None, dates: list[str] | None = None) -> pd.DataFrame:
    """批量抓取多个报告期的净利润与行业，返回长表。

    列：code, date(报告期), net_profit, industry。
    """
    as_of = pd.Timestamp(as_of or datetime.now())
    if dates is None:
        dates = _quarter_ends(as_of, n=9)
    rows = []
    for dt in dates:
        df = ak.stock_yjbb_em(date=dt)
        if df is None or df.empty:
            continue
        sub = df[["股票代码", "净利润-净利润", "所处行业"]].copy()
        sub["date"] = dt
        sub = sub.rename(columns={"股票代码": "code", "净利润-净利润": "net_profit", "所处行业": "industry"})
        sub["code"] = sub["code"].astype(str).str.zfill(6)
        rows.append(sub)
    if not rows:
        return pd.DataFrame(columns=["code", "date", "net_profit", "industry"])
    return pd.concat(rows, ignore_index=True)


def compute_ttm_earnings(earnings_long: pd.DataFrame, as_of: datetime | None = None) -> pd.DataFrame:
    """由业绩长表计算每只股票的 TTM 净利润、最新单季净利润、行业。

    报告期为"累计值"。正确 TTM 公式：
        TTM(截至最新季) = C(最新季) - C(去年同季) + FY(去年)
    最新单季 = C(最新季) - C(上一季累计)（Q1 则 = C(最新季)）。
    """
    if earnings_long.empty:
        return pd.DataFrame(columns=["code", "ttm_net_profit", "latest_q_net_profit", "industry"])
    earnings_long = earnings_long.drop_duplicates(subset=["code", "date"], keep="first")

    _QMAP = {"Q1": "0331", "Q2": "0630", "Q3": "0930", "Q4": "1231"}

    def qtype(d: str) -> str:
        m = int(d[4:6])
        return {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}[m]

    ind = earnings_long.sort_values("date").groupby("code")["industry"].last()

    results = {}
    for code, grp in earnings_long.groupby("code"):
        ser = grp.set_index("date")["net_profit"]
        ser = ser[ser.notna()]
        if ser.empty:
            continue
        latest = ser.index.max()
        y, m = int(latest[:4]), int(latest[4:6])
        qt = qtype(latest)
        c_latest = ser[latest]
        d_sameq_py = f"{y-1}{_QMAP[qt]}"
        c_sameq_py = ser.get(d_sameq_py, np.nan)
        d_fy_py = f"{y-1}1231"
        c_fy_py = ser.get(d_fy_py, np.nan)
        if pd.notna(c_latest) and pd.notna(c_sameq_py) and pd.notna(c_fy_py):
            ttm = c_latest - c_sameq_py + c_fy_py
        else:
            ttm = np.nan
        if qt == "Q1":
            latest_q = c_latest
        else:
            d_prevq = f"{y}{_QMAP[{'Q2': 'Q1', 'Q3': 'Q2', 'Q4': 'Q3'}[qt]]}"
            c_prevq = ser.get(d_prevq, np.nan)
            latest_q = c_latest - c_prevq if pd.notna(c_prevq) else np.nan
        results[code] = {"ttm_net_profit": ttm, "latest_q_net_profit": latest_q}

    out = pd.DataFrame.from_dict(results, orient="index").reset_index().rename(columns={"index": "code"})
    out["industry"] = out["code"].map(ind)
    return out


# ---------------------------------------------------------------------------
# 历史行情 / 流动性（A 股：Sina daily）
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
# 生产用市值（东财实时主机，本环境被墙；生产/ Tushare 时启用）
# ---------------------------------------------------------------------------
def fetch_a_market_cap_em() -> pd.DataFrame:
    """东财批量市值。本环境会被防火墙 RESET，生产环境可用。"""
    df = ak.stock_zh_a_spot_em()
    df = df.rename(columns={"代码": "code", "名称": "name", "总市值": "total_mcap", "流通市值": "float_mcap"})
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df[["code", "name", "total_mcap", "float_mcap"]]


# ---------------------------------------------------------------------------
# 各市场 listing 级快照
# ---------------------------------------------------------------------------
def build_a_listing_snapshot(as_of: datetime | None = None) -> pd.DataFrame:
    """A 股 listing 级快照（现价 Sina，净利润/行业 yjbb_em，流动性 Sina daily）。"""
    from ..sector.classifier import map_to_sector

    as_of = pd.Timestamp(as_of or datetime.now())
    ref = pd.read_csv(DATA_DIR / "demo_universe.csv", dtype={"code": str})
    ref["code"] = ref["code"].str.zfill(6)

    quotes = fetch_a_quotes_sina()
    qmap = quotes.set_index("code")[["price"]]

    earnings = fetch_a_earnings(as_of)
    earn = compute_ttm_earnings(earnings, as_of)

    start = (as_of - timedelta(days=200)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    _, volumes = fetch_hist(ref["code"].tolist(), start, end)
    float_shares = ref.set_index("code")["total_shares"] * ref.set_index("code")["iwf"]
    liq = compute_liquidity(volumes, float_shares)

    out = ref.copy()
    out["entity_id"] = ref["entity_id"]
    out["market"] = "A"
    out["curr"] = "CNY"
    out["is_st"] = out["name"].str.contains("ST", case=False, na=False)
    out["is_china"] = True
    out["price"] = out["code"].map(qmap["price"])
    out["total_shares"] = out["total_shares"].astype(float)
    out["iwf"] = out["iwf"].astype(float)
    out["float_shares"] = out["total_shares"] * out["iwf"]
    out["total_mcap_local"] = out["price"] * out["total_shares"]
    out["float_mcap_local"] = out["price"] * out["float_shares"]
    out["total_mcap"] = out["total_mcap_local"]  # A 股本币即 CNY
    out["float_mcap"] = out["float_mcap_local"]
    out["ttm_net_profit"] = out["code"].map(earn.set_index("code")["ttm_net_profit"])
    out["latest_q_net_profit"] = out["code"].map(earn.set_index("code")["latest_q_net_profit"])
    out["industry"] = out["code"].map(earn.set_index("code")["industry"])
    out["sector"] = out["industry"].map(map_to_sector)
    out["liquidity_ratio"] = out["code"].map(liq)
    out["listing_date"] = pd.to_datetime(out["listing_date"])
    out["shares_source"] = "reference"  # 人工核定参考股本（自由流通口径）
    out["profit_source"] = "em"  # yjbb 真实财报
    return out


_HK_PROFIT_ITEM = "股东应占溢利"  # 归母净利润科目（港股利润表）


def fetch_hk_profit_periods(code: str):
    """港股净利润披露期 [(start, end, value), ...]（年度+中期合并，累计口径）。

    金额为报告货币；本指数的中资股名单绝大多数以 CNY 报告（近似，个别
    USD 报告者如中芯国际存在未折算偏差，见 README 数据源说明）。失败返回 None。
    """
    def _fetch():
        parts = []
        for ind in ("年度", "中期"):
            try:
                df = ak.stock_financial_hk_report_em(
                    stock=str(code).zfill(5), symbol="利润表", indicator=ind
                )
            except Exception:  # noqa: BLE001
                continue
            if df is None or df.empty:
                continue
            sub = df[df["STD_ITEM_NAME"] == _HK_PROFIT_ITEM]
            for _, r in sub.iterrows():
                parts.append((
                    pd.Timestamp(r["START_DATE"]),
                    pd.Timestamp(r["REPORT_DATE"]),
                    float(r["AMOUNT"]),
                ))
        return pd.DataFrame(parts, columns=["start", "end", "value"]) if parts else None

    df = CACHE.get_or_fetch(f"hk_profit_{str(code)}", _fetch)
    if df is None or (hasattr(df, "empty") and df.empty):
        return None
    return [(r[0], r[1], r[2]) for r in df.itertuples(index=False, name=None)]


def apply_em_shares_local(out: pd.DataFrame, em: pd.DataFrame | None, fxr: float) -> pd.DataFrame:
    """用东财快照覆盖 HK/US listing 的价格/股本/市值（本币与 CNY），标记 shares_source。

    em 为 None 或个股缺失时保留静态参考值（shares_source=static）。
    """
    out = out.copy()
    out["shares_source"] = "static"
    if em is None or em.empty:
        return out
    m = em.set_index("code")
    hit = out["code"].isin(m.index)
    idx = out.index[hit]
    if not len(idx):
        return out
    price = out.loc[idx, "code"].map(m["price"]).astype(float)
    tm = out.loc[idx, "code"].map(m["total_mcap_local"]).astype(float)
    fm = out.loc[idx, "code"].map(m["float_mcap_local"]).astype(float)
    valid = price > 0
    idx = idx[valid]
    price, tm, fm = price[valid], tm[valid], fm[valid]
    out.loc[idx, "price"] = price.values
    out.loc[idx, "total_shares"] = (tm / price).values
    out.loc[idx, "float_shares"] = (fm / price).values
    out.loc[idx, "iwf"] = (fm / tm).values
    out.loc[idx, "total_mcap_local"] = tm.values
    out.loc[idx, "float_mcap_local"] = fm.values
    out.loc[idx, "total_mcap"] = (tm * fxr).values  # 折算 CNY
    out.loc[idx, "float_mcap"] = (fm * fxr).values
    out.loc[idx, "shares_source"] = "em"
    return out


def apply_real_earnings(out: pd.DataFrame, market: str, usd_cny: float) -> pd.DataFrame:
    """用真实财务源覆盖 TTM/最新单季：HK=东财港股财报，US=SEC EDGAR。

    调用前 out 已含静态兜底值（ttm_net_profit / latest_q_net_profit）。
    失败个股保留静态并标 profit_source=static。
    """
    out = out.copy()
    out["profit_source"] = "static"
    for i, r in out.iterrows():
        try:
            if market == "HK":
                periods = fetch_hk_profit_periods(r["code"])
                res = compute_ttm_from_periods(periods) if periods else None
                if res and res["ttm"] is not None:
                    out.loc[i, "ttm_net_profit"] = res["ttm"]
                    out.loc[i, "latest_q_net_profit"] = res["latest_q"]
                    out.loc[i, "profit_source"] = "em"
            else:
                res = edgar.fetch_us_net_income(r["code"], usd_cny=usd_cny)
                if res is not None:
                    out.loc[i, "ttm_net_profit"] = res["ttm"]
                    out.loc[i, "latest_q_net_profit"] = res["latest_q"]
                    out.loc[i, "profit_source"] = "edgar"
        except Exception:  # noqa: BLE001
            continue
    return out


def build_hk_us_listing_snapshot(as_of: datetime | None, market: str, fx_table: pd.DataFrame) -> pd.DataFrame:
    """港股/美股 listing 级快照。

    - 股本/市值：东财 push2 真实快照（需国内网络/VPN），缺失回落 demo CSV 静态参考；
    - 财务：HK=东财港股财报（TTM+单季），US=SEC EDGAR；失败回落静态；
    - 现价/成交量：Sina daily（东财不可达时价格仍以 Sina 为准，股本按静态计）。
    """
    as_of = pd.Timestamp(as_of or datetime.now())
    fname = "demo_hk.csv" if market == "HK" else "demo_us.csv"
    curr = "HKD" if market == "HK" else "USD"
    ref = pd.read_csv(DATA_DIR / fname, dtype={"code": str})

    codes = ref["code"].astype(str).tolist()
    prices = fetch_hk_us_price(codes, market, as_of)

    start = (as_of - timedelta(days=200)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    _, volumes = fetch_hk_us_hist(codes, market, start, end)
    float_shares = pd.Series(
        (ref["total_shares"].astype(float) * ref["iwf"].astype(float)).values,
        index=ref["code"].astype(str).values,
    )
    liq = compute_liquidity(volumes, float_shares)

    fxr = fx.fx_rate_on(fx_table, as_of, curr)

    out = ref.copy()
    out["entity_id"] = ref["entity_id"]
    out["market"] = market
    out["curr"] = curr
    out["is_st"] = out["name"].str.contains("ST", case=False, na=False)
    out["is_china"] = True
    out["price"] = out["code"].map(prices)
    out["total_shares"] = out["total_shares"].astype(float)
    out["iwf"] = out["iwf"].astype(float)
    out["float_shares"] = out["total_shares"] * out["iwf"]
    out["total_mcap_local"] = out["price"] * out["total_shares"]
    out["float_mcap_local"] = out["price"] * out["float_shares"]
    out["total_mcap"] = out["total_mcap_local"] * fxr  # 折算 CNY
    out["float_mcap"] = out["float_mcap_local"] * fxr
    out["ttm_net_profit"] = out["ttm_net_profit_cny"].astype(float)  # 静态兜底（已是 CNY）
    out["latest_q_net_profit"] = out["ttm_net_profit"]  # 兜底近似：单季=TTM
    out["sector"] = out["sector"]
    out["industry"] = out["sector"]
    out["liquidity_ratio"] = out["code"].map(liq)
    out["listing_date"] = pd.to_datetime(out["listing_date"])

    # 东财真实股本/市值覆盖（失败自动回落静态并标记）
    out = apply_em_shares_local(out, get_em_spot(market), fxr)
    # 真实财务覆盖（HK=东财财报 / US=EDGAR）
    out = apply_real_earnings(out, market, usd_cny=fx.fx_rate_on(fx_table, as_of, "USD"))
    return out


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
