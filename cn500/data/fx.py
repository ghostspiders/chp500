"""多币种折算（基准货币 CNY）。

数据源（本环境已验证可达）：
  - ak.currency_boc_sina(symbol='美元'/'港币', start_date, end_date)
    → 中国银行历史汇率，取「央行中间价 / 100」即 CNY per 1 单位外币。

回退策略：若接口不可达（如生产网络隔离），使用 config 中的静态近似汇率。
返回统一语义：**CNY per 1 单位外币**（USD→CNY≈7.1，HKD→CNY≈0.91）。
"""

from __future__ import annotations

from datetime import datetime

import akshare as ak
import pandas as pd

from ..config import CONFIG

# 中行 currency_boc_sina 的 symbol 中文名
_BOC_SYMBOL = {"USD": "美元", "HKD": "港币"}

# 静态回退汇率（CNY per 1 单位）；当实时接口不可达时启用
_STATIC_FX = {"USD": 7.10, "HKD": 0.91}


def _fetch_one(currency: str, start: str, end: str) -> pd.Series:
    """抓取单一币种的历史汇率，返回 index=date、value=CNY per 单位的 Series。"""
    sym = _BOC_SYMBOL.get(currency)
    if sym is None:
        raise ValueError(f"不支持的币种: {currency}（仅支持 {list(_BOC_SYMBOL)}）")
    df = ak.currency_boc_sina(symbol=sym, start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.Series(dtype="float64")
    df = df.copy()
    df["date"] = pd.to_datetime(df["日期"], errors="coerce")
    # 央行中间价 为「每 100 外币兑人民币」，故 /100
    mid = pd.to_numeric(df["央行中间价"], errors="coerce")
    s = (mid / 100.0).rename(currency)
    s.index = df["date"]
    s = s[s.notna()]
    return s.sort_index()


def fetch_fx_history(currencies, start: str, end: str) -> pd.DataFrame:
    """抓取多币种日频汇率表。

    返回 DataFrame：index=日期，columns=币种，值=CNY per 1 单位。
    任一币种接口失败时以静态值向前填充，保证不中断流程。
    """
    start_s = pd.Timestamp(start).strftime("%Y%m%d")
    end_s = pd.Timestamp(end).strftime("%Y%m%d")
    parts = {}
    for cur in currencies:
        try:
            s = _fetch_one(cur, start_s, end_s)
        except Exception:  # noqa: BLE001
            s = pd.Series(dtype="float64")
        if s.empty:
            # 静态回退：构造全窗口常量序列
            idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
            s = pd.Series(_STATIC_FX.get(cur, float("nan")), index=idx, name=cur)
        parts[cur] = s
    table = pd.concat(parts.values(), axis=1, join="outer")
    table.columns = list(parts.keys())
    # 前向填充（节假日无报价），再后向填充首尾缺口
    table = table.ffill().bfill()
    # 任何残留缺口用静态值补
    for cur in table.columns:
        table[cur] = table[cur].fillna(_STATIC_FX.get(cur, float("nan")))
    return table


def fx_rate_on(table: pd.DataFrame, as_of: datetime, currency: str) -> float:
    """取 as_of 当日或之前最近一个交易日的汇率（CNY per 1 单位）。"""
    as_of = pd.Timestamp(as_of)
    if currency == "CNY":
        return 1.0
    if table is None or table.empty or currency not in table.columns:
        return _STATIC_FX.get(currency, float("nan"))
    sub = table.loc[:as_of, currency]
    sub = sub[sub.notna()]
    if sub.empty:
        return _STATIC_FX.get(currency, float("nan"))
    return float(sub.iloc[-1])
