"""离线验证连续指数的核心不变式：篮子变动（再平衡/IWF 刷新）时指数在过渡日无跳空。

不依赖 akshare / 网络：将 fetch_cny_prices 替换为确定性合成行情。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chp500.index import persistent as pidx  # noqa: E402


def _deterministic_prices(entity_ids, start, end):
    dates = pd.bdate_range(start, end)
    data = {}
    for i, e in enumerate(entity_ids):
        base = 10.0 + i
        vals = []
        for k, d in enumerate(dates):
            # 纯函数（同进程/跨进程一致），避免 hash 随机化
            seed = (int(d.strftime("%Y%m%d")) * 31 + i * 7) % 100
            vals.append(base * (1 + 0.0005 * k) + 0.01 * seed / 100.0)
        data[e] = vals
    return pd.DataFrame(data, index=dates)


def _fake_fetch(basket, start, end):
    return _deterministic_prices(list(basket["entity_id"]), start, end)


def _basket(entities_shares):
    rows = []
    for eid, fs in entities_shares:
        rows.append({"entity_id": eid, "code": eid.split(".")[-1], "name": eid,
                     "market": eid.split(".")[0], "curr": "CNY",
                     "sector": "X", "industry": "Y", "price": 1.0,
                     "total_shares": fs, "float_shares": fs, "float_mcap": fs,
                     "iwf": 1.0, "ttm_net_profit": 1.0, "liquidity_ratio": 1.0,
                     "weight": 0.5, "shares_source": "t", "profit_source": "t"})
    return pd.DataFrame(rows)


def _levels(con):
    return pd.read_sql_query(
        "SELECT date, price_index, divisor, rebalance_as_of FROM index_levels ORDER BY date",
        con)


def test_continuity():
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    con = pidx.open_db(tmp)
    pidx.fetch_cny_prices = _fake_fetch  # 替换行情源

    # 1) 建库：篮子1 = {e1, e2}
    b1 = _basket([("A.e1", 1.0), ("A.e2", 1.0)])
    pidx.ensure_inception(con, b1, "2024-01-31", "2024-01-01", base_point=1000.0)
    lv = _levels(con)
    assert abs(lv.iloc[0]["price_index"] - 1000.0) < 1e-6, "基期应为 1000"
    print(f"[ok] 建库：{len(lv)} 个交易日，起点 {lv.iloc[0]['price_index']:.4f}")

    # 2) 补点：as_of 推进到 2024-02-15（同一篮子，divisor 不变）
    pidx.append_daily(con, b1, "2024-02-15")
    lv = _levels(con)
    d0_div = lv.iloc[0]["divisor"]
    # 同一再平衡区间内 divisor 应恒定
    same_div = (lv["divisor"] == d0_div).all()
    assert same_div, "补点阶段 divisor 应保持不变"
    print(f"[ok] 补点后共 {len(lv)} 个交易日，divisor 恒定={d0_div:.4f}")

    # 3) 篮子变动：as_of=2024-01-15（落在已存储区间内）改为篮子2（e1 股本翻倍 + 新增 e3）
    b2 = _basket([("A.e1", 2.0), ("A.e2", 1.0), ("A.e3", 1.0)])
    before = _levels(con)
    pidx.apply_basket_change(con, b2, "2024-01-15", kind="rebalance")
    after = _levels(con)

    # 过渡日 T=2024-01-15 的净值应等于其前一交易日净值（无跳空）
    ts = pd.to_datetime(after["date"])
    mask_T = ts == pd.Timestamp("2024-01-15")
    mask_prev = ts == pd.Timestamp("2024-01-15") - pd.Timedelta(days=1)
    # 前一交易日（营业日）在 bdate_range 中即为 2024-01-12
    prev_row = after[ts == pd.Timestamp("2024-01-12")].iloc[0]
    T_row = after[mask_T].iloc[0]
    jump = abs(T_row["price_index"] - prev_row["price_index"])
    assert jump < 1e-6, f"过渡日应无跳空，实际跳变 {jump}"
    print(f"[ok] 篮子变动后过渡日无跳空（跳变={jump:.2e}），divisor 由 "
          f"{prev_row['divisor']:.4f} 调整为 {T_row['divisor']:.4f}")

    # 区间完整性：重新计算覆盖了 [T, end]，且天数不少于重算前
    assert len(after) >= len(before), "重算不应丢失交易日"
    print(f"[ok] 重算后 {len(after)} 个交易日（重算前 {len(before)}）")

    # 4) 再次补点（新篮子）应继续连续
    pidx.append_daily(con, b2, "2024-02-20")
    final = _levels(con)
    last = final.iloc[-1]["price_index"]
    print(f"[ok] 终态：{len(final)} 个交易日，末值 {last:.4f}")

    print("\nALL CONTINUITY TESTS PASSED")


if __name__ == "__main__":
    test_continuity()
