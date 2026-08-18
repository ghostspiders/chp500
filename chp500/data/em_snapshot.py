"""东财 push2 全市场快照：三市场总市值/流通市值的真实数据源。

- A：沪深主板/创业板/科创板（不含北交所，与方法论 include_bse=false 一致）
- HK：全部港股
- US：纳斯达克/纽交所/美交所

接口返回本币市值（A=CNY、HK=HKD、US=USD），本模块只做抓取与规整，
汇率折算由调用方（adapters/universe）处理。

网络约束：东财 push2 主机需国内网络或 VPN；任一页失败重试 3 次后整体
返回 None，调用方必须降级（回落静态/合成值并标记 shares_source）。
"""

from __future__ import annotations

import time

import pandas as pd

from ..config import CONFIG
from .cache import Cache

_MARKET_CFG = {
    # 与探测验证过的 host/fs 参数一一对应
    "A": ("82.push2.eastmoney.com", "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"),
    "HK": ("95.push2.eastmoney.com", "m:128+t:1,m:128+t:2,m:128+t:3,m:128+t:4"),
    "US": ("65.push2.eastmoney.com", "m:105,m:106,m:107"),
}

_COLUMNS = ["code", "name", "price", "total_mcap_local", "float_mcap_local"]

# 行情快照缓存 1 天（股本=市值/价，比例稳定；价格新鲜度够用）
_CACHE = Cache(CONFIG["cache_dir"], ttl_days=CONFIG.get("em_spot_ttl_days", 1))


def _fetch_page(host: str, fs: str, page: int, page_size: int = 1000) -> dict | None:
    """拉取一页 clist；返回解析后的 JSON dict，失败返回 None。"""
    import requests

    url = f"https://{host}/api/qt/clist/get"
    params = {
        "pn": page, "pz": page_size, "po": 1, "np": 1,
        "fltt": 2, "invt": 2, "fid": "f3", "fs": fs,
        "fields": "f12,f14,f2,f20,f21",
    }
    for _ in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if data.get("rc") == 0 and data.get("data"):
                    return data
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    return None


def _parse_rows(diff: list) -> list[dict]:
    """过滤无效行（停牌/权证等市值为 '-'），规整为标准列。"""
    rows = []
    for d in diff:
        try:
            price = d.get("f2")
            tm = d.get("f20")
            fm = d.get("f21")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            if not isinstance(tm, (int, float)) or tm <= 0:
                continue
            if not isinstance(fm, (int, float)) or fm <= 0:
                continue
            rows.append({
                "code": str(d.get("f12", "")),
                "name": str(d.get("f14", "")),
                "price": float(price),
                "total_mcap_local": float(tm),
                "float_mcap_local": float(fm),
            })
        except Exception:  # noqa: BLE001
            continue
    return rows


def fetch_em_spot(market: str) -> pd.DataFrame | None:
    """分页拉取全市场快照；失败返回 None（调用方降级）。"""
    if market not in _MARKET_CFG:
        raise ValueError(f"unknown market: {market}")
    host, fs = _MARKET_CFG[market]
    rows: list[dict] = []
    total = None
    page = 1
    while total is None or len(rows) < total:
        data = _fetch_page(host, fs, page)
        if data is None:
            return None
        d = data["data"]
        total = int(d.get("total", 0))
        diff = d.get("diff") or []
        rows.extend(_parse_rows(diff))
        if not diff:
            break
        page += 1
    if not rows:
        return None
    return pd.DataFrame(rows, columns=_COLUMNS)


def get_em_spot(market: str) -> pd.DataFrame | None:
    """带缓存的市场快照（缓存 miss 且抓取失败时返回 None）。"""
    return _CACHE.get_or_fetch(f"em_spot_{market}", fetch_em_spot, market)
