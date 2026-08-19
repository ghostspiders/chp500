"""雪球个股信息直连客户端（A 股行业）。

- 行业：f10/cn/company.json 的 affiliate_industry.ind_name（中文行业名，
  如"白酒"/"银行"），供 GICS 风格板块关键词映射。
- 接口需 cookie token：先 GET https://xueqiu.com/about 引导（浏览器 UA），
  复用会话。单只失败返回 None（不计入结果）。
- 行业分类稳定，结果走全局 parquet/JSON 缓存（cache_ttl_days）。
"""

from __future__ import annotations

import requests

from ..config import CONFIG
from .cache import Cache
from .sources import source_url, source_urls

# 接口地址（config.yaml: data_sources.xueqiu_info 的 base_url/urls 可改址覆盖）
_BASE = source_url("xueqiu_info", "https://stock.xueqiu.com").rstrip("/")
_URLS = source_urls("xueqiu_info", {
    "bootstrap": "https://xueqiu.com/about",
    "industry": "/v5/stock/f10/cn/company.json",
})

_CACHE = Cache(CONFIG["cache_dir"], ttl_days=CONFIG.get("cache_ttl_days", 7))

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": _UA})
        try:
            s.get(_URLS["bootstrap"], timeout=15)  # 引导 cookie token
        except Exception:  # noqa: BLE001
            pass
        _session = s
    return _session


def _a_symbol(code: str) -> str:
    code = str(code).strip()
    return ("SH" + code) if code[:1] == "6" else ("SZ" + code)


def _fetch_one(code: str) -> str | None:
    try:
        r = _get_session().get(
            _BASE + "/" + _URLS["industry"].lstrip("/"),
            params={"symbol": _a_symbol(code)},
            headers={"Referer": "https://xueqiu.com/"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        company = (r.json().get("data") or {}).get("company") or {}
        ind = company.get("affiliate_industry") or {}
        name = ind.get("ind_name")
        return str(name) if name else None
    except Exception:  # noqa: BLE001
        return None


def fetch_a_industry(codes) -> dict[str, str]:
    """A 股行业（雪球 affiliate_industry），返回 {code: 行业中文}。

    逐只抓取（结果缓存）；单只失败不计入，由调用方决定剔除或置"其他"。
    """
    out: dict[str, str] = {}
    for c in codes:
        code = str(c).strip()
        v = _CACHE.get_or_fetch(f"xq_industry_{_a_symbol(code)}",
                                lambda c=code: _fetch_one(c))
        if v:
            out[code] = v
    return out
