"""图表构建（plotly，纯函数，可在无 streamlit 环境下单元测试）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ..config import BASE_DIR


def load_outputs(out_dir: str | Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    out_dir = Path(out_dir or BASE_DIR / "outputs")
    constituents = pd.read_csv(out_dir / "constituents.csv")
    index = pd.read_csv(out_dir / "index.csv")
    index["date"] = pd.to_datetime(index["date"])
    return constituents, index


def fig_index(index: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=index["date"], y=index["price_index"], name="价格指数"))
    fig.add_trace(go.Scatter(x=index["date"], y=index["total_return"], name="全收益指数"))
    fig.update_layout(title="CHP 500 指数走势（基点 1000）", xaxis_title="日期",
                      yaxis_title="指数点位", height=420)
    return fig


def fig_top_weights(constituents: pd.DataFrame, n: int = 20) -> go.Figure:
    top = constituents.sort_values("weight", ascending=False).head(n).iloc[::-1]
    fig = px.bar(top, x="weight", y="name", orientation="h",
                 text="weight", title=f"个股权重 TOP {n}")
    fig.update_traces(texttemplate="%{text:.2%}", textposition="outside")
    fig.update_layout(height=520, xaxis_tickformat=".0%")
    return fig


def fig_sector(constituents: pd.DataFrame) -> go.Figure:
    sw = constituents.groupby("sector")["weight"].sum().sort_values(ascending=False)
    fig = px.pie(values=sw.values, names=sw.index, title="行业权重分布", hole=0.35)
    fig.update_traces(texttemplate="%{label}<br>%{percent:.1%}")
    return fig


def fig_market_weights(constituents: pd.DataFrame) -> go.Figure:
    mw = constituents.groupby("market")["weight"].sum()
    fig = px.bar(x=mw.index, y=mw.values, text=mw.values, title="市场权重分布")
    fig.update_traces(texttemplate="%{text:.1%}")
    fig.update_layout(yaxis_tickformat=".0%")
    return fig
