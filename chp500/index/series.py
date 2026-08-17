"""指数点位与全收益序列（方法论 §7.3 除数、§9 指数序列）。

价格指数： level_t = Σ(price_t * float_shares) / divisor
全收益指数： 在价格指数基础上，将分红按除息日再投资。
除数调整：   成分/股本变动时调整 divisor，使指数连续（不跳空）。
"""

from __future__ import annotations

import pandas as pd


def initial_divisor(total_float_mcap: float, base_point: float = 1000.0) -> float:
    """基期除数 = 基期总自由流通市值 / 基点。"""
    return total_float_mcap / base_point


def rebase_divisor(prev_level: float, new_total_float_mcap: float) -> float:
    """再平衡/公司行为时，使指数在过渡日连续的新除数。"""
    if prev_level <= 0:
        return new_total_float_mcap / 1000.0
    return new_total_float_mcap / prev_level


def price_index(prices: pd.DataFrame, float_shares: pd.Series, divisor: float) -> pd.Series:
    """prices: 列=entity_id，行=日期，值为现价。float_shares: Index=entity_id。"""
    aligned = prices.reindex(columns=float_shares.index)
    market_value = aligned.mul(float_shares, axis=1).sum(axis=1)
    return market_value / divisor


def total_return_index(
    prices: pd.DataFrame, float_shares: pd.Series, divisor: float, dividends: pd.DataFrame | None = None
) -> pd.Series:
    """全收益指数：分红再投资。

    dividends: 与 prices 同形的每股分红（仅在除息日有值），单位与价格一致。
    实现：以价格指数市值为基础，每日按各股权息率复利再投资。
    """
    aligned_p = prices.reindex(columns=float_shares.index)
    aligned_d = dividends.reindex(columns=float_shares.index) if dividends is not None else None

    shares = float_shares.copy()  # 再投资导致"虚拟持股"增加
    tr_level = []
    prev_mv = None
    for date, row in aligned_p.iterrows():
        # 当日市值（含再投资增持）
        mv = (row * shares).sum()
        if prev_mv is None or prev_mv <= 0:
            level = mv / divisor
        else:
            level = tr_level[-1] * (mv / prev_mv)
        # 分红再投资：div_i / price_i 增持 shares
        if aligned_d is not None:
            drow = aligned_d.loc[date]
            for ent in shares.index:
                p = row[ent]
                d = drow[ent]
                if pd.notna(d) and d > 0 and p > 0:
                    shares[ent] += shares[ent] * (d / p)
        tr_level.append(level)
        prev_mv = mv
    return pd.Series(tr_level, index=aligned_p.index, name="total_return")


def build_series(
    prices: pd.DataFrame, float_shares: pd.Series, dividends: pd.DataFrame | None = None, base_point: float = 1000.0
) -> pd.DataFrame:
    """一次性构建价格 + 全收益序列（用于静态演示/回测）。"""
    first_mv = (prices.iloc[0] * float_shares).sum()
    divisor = initial_divisor(first_mv, base_point)
    pi = price_index(prices, float_shares, divisor)
    tri = total_return_index(prices, float_shares, divisor, dividends)
    return pd.DataFrame({"price_index": pi, "total_return": tri})
