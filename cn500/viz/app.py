"""CN 500 指数可视化看板（streamlit）。

运行： streamlit run cn500/viz/app.py
依赖 outputs/ 下由 scripts/build_index.py 生成的 constituents.csv 与 index.csv。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cn500.viz import charts  # noqa: E402
from cn500.config import CONFIG  # noqa: E402


def main():
    st.set_page_config(page_title="CN 500 指数看板", layout="wide")
    st.title("中国500指数（China 500）编制看板")

    out_dir = st.sidebar.text_input("outputs 目录", str(Path(__file__).resolve().parent.parent.parent / "outputs"))
    try:
        constituents, index = charts.load_outputs(out_dir)
    except FileNotFoundError:
        st.error("未找到 outputs 文件，请先运行 `python scripts/build_index.py --mode demo`。")
        return

    latest = index.iloc[-1]
    col1, col2, col3 = st.columns(3)
    col1.metric("成分数量", len(constituents))
    col2.metric("价格指数（最新）", f"{latest['price_index']:.1f}")
    col3.metric("全收益指数（最新）", f"{latest['total_return']:.1f}")

    st.subheader("指数走势")
    st.plotly_chart(charts.fig_index(index), use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("行业权重")
        st.plotly_chart(charts.fig_sector(constituents), use_container_width=True)
    with c2:
        st.subheader("市场权重")
        st.plotly_chart(charts.fig_market_weights(constituents), use_container_width=True)

    st.subheader("个股权重 TOP 20")
    st.plotly_chart(charts.fig_top_weights(constituents, 20), use_container_width=True)

    st.subheader("成分明细")
    show = constituents.copy()
    show["float_mcap(亿元)"] = (show["float_mcap"] / 1e8).round(1)
    show["weight"] = (show["weight"] * 100).round(2)
    st.dataframe(
        show[["code", "name", "market", "sector", "industry", "float_mcap(亿元)",
              "iwf", "ttm_net_profit", "liquidity_ratio", "weight", "single_exceed"]]
        .rename(columns={"weight": "权重%", "single_exceed": "超单股上限"}),
        use_container_width=True,
    )

    with st.sidebar:
        st.caption(f"single_cap_mode={CONFIG.get('single_cap_mode')} "
                   f"| cap_single={CONFIG.get('cap_single')} "
                   f"| sector_cap_mode={CONFIG.get('sector_cap_mode')}")


if __name__ == "__main__":
    main()
