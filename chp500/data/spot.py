"""腾讯行情快照（qt.gtimg.cn）：三市场总市值/流通市值的真实数据源。

- A：sh/sz 前缀；HK：hk+5 位；US：us+代码
- 接口返回本币市值（A=CNY、HK=HKD、US=USD），本模块只做抓取与规整，
  汇率折算由调用方（adapters/universe）处理。

严格真实模式：不可达/无有效行返回 None，由调用方报错终止，绝不回落
静态/合成近似（原东财 push2 通路实测不可达，已移除，本源为唯一直接源）。
"""

from __future__ import annotations

import re

import pandas as pd
import requests

_COLUMNS = ["code", "name", "price", "total_mcap_local", "float_mcap_local", "pe_ttm"]


def _tencent_symbol(code: str, market: str) -> str:
    """把内部代码映射为腾讯 qt 查询符号（sh/sz/hk/us 前缀）。"""
    code = str(code).strip()
    if market == "A":
        return ("sh" + code) if code[:1] == "6" else ("sz" + code)
    if market == "HK":
        return "hk" + code.zfill(5)
    if market == "US":
        return "us" + code.upper()
    raise ValueError(f"unknown market: {market}")


def _tencent_clean_code(key: str, market: str) -> str:
    """从腾讯返回键（sh600519 / hk00700 / usBABA）提取内部代码。"""
    if market == "US":
        return key[2:].upper()
    return re.sub(r"[^0-9]", "", key)


def fetch_spot(market: str, codes) -> pd.DataFrame | None:
    """腾讯 qt.gtimg.cn 快照：现价/总市值/流通市值（本币）。

    市值字段语义随市场不同（已用股本字段×现价实测验证三市场）：
      - A 股/美股：f[44]=流通市值、f[45]=总市值（单位：亿本币）
      - 港股：f[44]==f[45]=总市值（腾讯不提供港股自由流通数据，
        流通市值以总市值近似，IWF 恒为 1，见 README 已知限制）

    折算为元；仅在价格与市值字段均有效时计入；返回 None 表示源不可用，
    调用方应报错终止（严格真实模式，无近似回落）。
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
                tm_yi = float(f[45])
                fm_yi = float(f[45]) if market == "HK" else float(f[44])
            except (ValueError, IndexError):
                continue
            try:
                pe_ttm = float(f[39])  # 亏损股为负值；缺失("-")置 NaN，仅影响净利推导
            except (ValueError, IndexError):
                pe_ttm = float("nan")
            if price <= 0 or tm_yi <= 0 or fm_yi <= 0:
                continue
            rows.append({
                "code": _tencent_clean_code(m.group(1), market),
                "name": f[1],
                "price": price,
                "total_mcap_local": tm_yi * 1e8,
                "float_mcap_local": fm_yi * 1e8,
                "pe_ttm": pe_ttm,
            })
    if not rows:
        return None
    return pd.DataFrame(rows, columns=_COLUMNS)
