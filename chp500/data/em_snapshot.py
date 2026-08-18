"""东财 push2 全市场快照：三市场总市值/流通市值的真实数据源。

- A：沪深主板/创业板/科创板（不含北交所，与方法论 include_bse=false 一致）
- HK：全部港股
- US：纳斯达克/纽交所/美交所

接口返回本币市值（A=CNY、HK=HKD、US=USD），本模块只做抓取与规整，
汇率折算由调用方（adapters/universe）处理。

网络约束：东财 push2 主机需国内网络或 VPN；任一页失败重试 3 次后整体
返回 None，调用方须报错终止（严格真实模式，绝不回落静态/合成近似）。
"""

from __future__ import annotations

import re
import time

import pandas as pd
import requests

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
    """分页拉取全市场快照；失败返回 None（调用方须报错终止，绝不回落近似）。"""
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


def _tencent_symbol(code: str, market: str) -> str:
    """把内部代码映射为腾讯 qt 查询符号（sh/sz/hk/us 前缀）。"""
    code = str(code).strip()
    if market == "A":
        return ("sh" + code) if code[:1] == "6" else ("sz" + code)
    if market == "HK":
        return "hk" + code.zfill(5)
    return "us" + code.upper()


def _tencent_clean_code(key: str, market: str) -> str:
    """从腾讯返回键（sh600519 / hk00700 / usBABA）提取内部代码。"""
    if market == "US":
        return key[2:].upper()
    return re.sub(r"[^0-9]", "", key)


def fetch_fallback_spot(market: str, codes) -> pd.DataFrame | None:
    """腾讯行情兜底：用 qt.gtimg.cn 取总市值/流通市值（亿，本币），折算为元。

    仅在市值/股本字段可用时计入；东财不可达时作为严格真实模式的兜底源，
    不再回落合成/静态近似。返回列与东财快照一致：
    code, name, price, total_mcap_local, float_mcap_local。
    """
    tq = [_tencent_symbol(c, market) for c in codes]
    rows = []
    for i in range(0, len(tq), 150):
        chunk = tq[i:i + 150]
        try:
            r = requests.get("https://qt.gtimg.cn/q=" + ",".join(chunk), timeout=25)
            txt = r.content.decode("gbk", "ignore")
        except Exception:  # noqa: BLE001
            continue
        for line in txt.split(";"):
            m = re.match(r'v_(\w+)="(.*)"', line.strip())
            if not m:
                continue
            f = m.group(2).split("~")
            if len(f) <= 45:
                continue
            try:
                price = float(f[3])
                tm = float(f[44])
                fm = float(f[45])
            except (ValueError, IndexError):
                continue
            if price <= 0 or tm <= 0 or fm <= 0:
                continue
            rows.append({
                "code": _tencent_clean_code(m.group(1), market),
                "name": f[1],
                "price": price,
                # 腾讯市值字段单位为"亿（本币）"，折算为元以与东财快照单位一致
                "total_mcap_local": tm * 1e8,
                "float_mcap_local": fm * 1e8,
            })
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["code", "name", "price",
                                       "total_mcap_local", "float_mcap_local"])


def _full_a_codes():
    from . import adapters
    return adapters.fetch_a_universe()["code"].astype(str).tolist()


def _full_hk_codes():
    import akshare as ak
    return ak.stock_hk_spot()["代码"].astype(str).tolist()


def get_em_spot(market: str, codes=None) -> pd.DataFrame | None:
    """市场快照：优先东财 push2（严格真实），不可达时回退腾讯行情（含市值）。

    - 东财可达：返回其全市场快照（codes 参数忽略）。
    - 东财不可达：用腾讯 qt.gtimg.cn 兜底；codes 为需覆盖的代码列表。
      未提供时 A/HK 自动取全量列表，US 必须提供（否则报错）。
    返回 None 表示所有来源均不可用，调用方应报错终止。
    """
    em = fetch_em_spot(market)
    if em is not None and not em.empty:
        return em
    # 东财不可达 → 腾讯兜底
    if codes is None:
        if market == "A":
            codes = _full_a_codes()
        elif market == "HK":
            codes = _full_hk_codes()
        else:
            raise RuntimeError(
                "东财 push2（US 快照）不可达且未提供 US 参考代码，腾讯兜底失败。"
            )
    print(f"[em] 东财 push2 不可达，使用腾讯行情兜底（{market}，{len(codes)} 只）")
    return fetch_fallback_spot(market, list(codes))
