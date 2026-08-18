"""SEC EDGAR 美股中概净利数据（权威、免认证）。

- CIK 解析：运行时拉取 SEC 官方 company_tickers.json；失败则无可回落（返回 None，交由调用方剔除该成分）。
- 净利：XBRL companyconcept（us-gaap:NetIncomeLoss，缺失时试 ifrs-full）。
- 口径：披露币种优先 CNY（多数中概 ADR 双币披露），否则 USD × 汇率折算。
- 财年错位（如阿里 3 月财年）由 ttm_periods 的期间匹配天然处理。

SEC 要求请求方自报身份（User-Agent）；限速 10 req/s，16 只 ADR 无压力。
"""

from __future__ import annotations

import time

import pandas as pd

from ..config import CONFIG
from .cache import Cache
from .ttm_periods import compute_ttm_from_periods

_UA = "chp500-index-research/0.1 (research project; contact: dev@chp500.local)"
_BASE = "https://data.sec.gov/api/xbrl/companyconcept"

_CACHE = Cache(CONFIG["cache_dir"], ttl_days=CONFIG.get("cache_ttl_days", 7))


def _get_json(url: str) -> dict | None:
    import requests

    for _ in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    return None


def _ticker_cik_map() -> dict[str, int]:
    data = _CACHE.get_or_fetch("edgar_ticker_map", lambda: _fetch_ticker_map())
    if data is None:
        return {}
    return {str(k).upper(): int(v) for k, v in data.items()}


def _fetch_ticker_map() -> dict | None:
    data = _get_json("https://www.sec.gov/files/company_tickers.json")
    if not isinstance(data, dict):
        return None
    out = {}
    for item in data.values():
        ticker = str(item.get("ticker", "")).upper()
        cik = int(item.get("cik_str", 0))
        if ticker and cik:
            out[ticker] = cik
    return out or None


def resolve_cik(ticker: str) -> int | None:
    ticker = ticker.upper()
    try:
        m = _ticker_cik_map()
    except Exception:  # noqa: BLE001
        return None
    return m.get(ticker)


def _extract_periods(concept: dict) -> tuple[list, str] | None:
    """从 companyconcept JSON 提取 (periods, currency)；优先 CNY 单位。"""
    units = concept.get("units") or {}
    for cur in ("CNY", "USD"):
        entries = units.get(cur)
        if entries:
            periods = [
                (pd.Timestamp(e["start"]), pd.Timestamp(e["end"]), float(e["val"]))
                for e in entries
                if e.get("val") is not None
            ]
            if periods:
                return periods, cur
    return None


def _fetch_concept(cik: int, taxonomy: str) -> dict | None:
    url = f"{_BASE}/CIK{cik:010d}/{taxonomy}/NetIncomeLoss.json"
    return _get_json(url)


def fetch_us_net_income(ticker: str, usd_cny: float = 7.10) -> dict | None:
    """返回 {ttm, latest_q, granularity, latest_end, currency}（均为 CNY）。

    拉取/解析任一环节失败返回 None，由调用方标记 missing 并剔除该成分。
    """
    cik = resolve_cik(ticker)
    if not cik:
        return None
    fetched = _CACHE.get_or_fetch(
        f"edgar_ni_{ticker}", lambda: _fetch_concept(cik, "us-gaap")
    )
    if fetched is None:
        fetched = _CACHE.get_or_fetch(
            f"edgar_ni_{ticker}_ifrs", lambda: _fetch_concept(cik, "ifrs-full")
        )
    if not isinstance(fetched, dict):
        return None
    extracted = _extract_periods(fetched)
    if extracted is None:
        return None
    periods, cur = extracted
    res = compute_ttm_from_periods(periods)
    if res is None:
        return None
    rate = 1.0 if cur == "CNY" else float(usd_cny or 7.10)
    out = {
        "ttm": res["ttm"] * rate if res["ttm"] is not None else None,
        "latest_q": res["latest_q"] * rate,
        "granularity": res["granularity"],
        "latest_end": res["latest_end"],
        "currency": cur,
    }
    if out["ttm"] is None:
        return None
    return out
