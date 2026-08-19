"""腾讯行情快照（spot）单元测试：符号映射、响应解析、字段语义、无效行过滤、失败终止。

字段语义（已实测三市场验证）：A/US 的 f[44]=流通市值、f[45]=总市值；
HK 的 f[44]==f[45]=总市值（腾讯无港股自由流通数据，流通以总市值近似）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from chp500.data import spot


def test_tencent_symbol_mapping():
    assert spot._tencent_symbol("600519", "A") == "sh600519"
    assert spot._tencent_symbol("000001", "A") == "sz000001"
    assert spot._tencent_symbol("300750", "A") == "sz300750"
    assert spot._tencent_symbol("00700", "HK") == "hk00700"
    assert spot._tencent_symbol("700", "HK") == "hk00700"  # 补零到 5 位
    assert spot._tencent_symbol("baba", "US") == "usBABA"


def test_tencent_clean_code():
    assert spot._tencent_clean_code("sh600519", "A") == "600519"
    assert spot._tencent_clean_code("hk00700", "HK") == "00700"
    assert spot._tencent_clean_code("usBABA", "US") == "BABA"


def _qt_line(sym: str, name: str, price, fm_yi, tm_yi, pe=15.0, n_fields: int = 50) -> str:
    # f[39]=PE(TTM)（亏损为负）、f[44]=流通市值（亿）、f[45]=总市值（亿）--A/US 语义；HK 两者相等。
    # n_fields 可小于 46 以构造"行太短"场景（此时跳过高位字段赋值）。
    f = ["1"] * n_fields
    f[1] = name
    f[2] = "000000"
    f[3] = str(price)
    if n_fields > 39:
        f[39] = str(pe)
    if n_fields > 44:
        f[44] = str(fm_yi)
    if n_fields > 45:
        f[45] = str(tm_yi)
    return f'v_{sym}="' + "~".join(f) + '";'


class _Resp:
    def __init__(self, text: str):
        self.content = text.encode("gbk")


def test_fetch_spot_a_fields_and_units(monkeypatch):
    # A 股：f[44]=流通市值 -> float、f[45]=总市值 -> total；f[39]=PE(TTM)；单位亿 -> 元
    text = _qt_line("sh601398", "工商银行", 7.67, fm_yi=20679, tm_yi=27336, pe=7.36)
    monkeypatch.setattr(spot.requests, "get", lambda url, timeout=25: _Resp(text))
    df = spot.fetch_spot("A", ["601398"])
    assert df is not None
    row = df.iloc[0]
    assert row["code"] == "601398"
    assert row["name"] == "工商银行"
    assert row["price"] == 7.67
    assert row["total_mcap_local"] == 27336 * 1e8
    assert row["float_mcap_local"] == 20679 * 1e8
    assert row["float_mcap_local"] <= row["total_mcap_local"]  # 回归：旧映射曾致流通>总
    assert row["pe_ttm"] == 7.36  # 下游 TTM 净利 = 总市值/PE(TTM)
    assert set(df.columns) == set(spot._COLUMNS)


def test_fetch_spot_us_fields(monkeypatch):
    text = _qt_line("usBABA", "阿里巴巴", 124.71, fm_yi=2934.65, tm_yi=2989.14, pe=19.56)
    monkeypatch.setattr(spot.requests, "get", lambda url, timeout=25: _Resp(text))
    df = spot.fetch_spot("US", ["BABA"])
    row = df.iloc[0]
    assert row["code"] == "BABA"
    assert row["total_mcap_local"] == 2989.14 * 1e8
    assert row["float_mcap_local"] == 2934.65 * 1e8
    assert row["pe_ttm"] == 19.56


def test_fetch_spot_keeps_negative_pe(monkeypatch):
    # 亏损股 PE(TTM) 为负值：必须保留（下游 总市值/PE -> 负 TTM 净利，盈利筛选剔除）
    text = _qt_line("sz002739", "儒意电影", 8.81, fm_yi=100, tm_yi=110, pe=-81.20)
    monkeypatch.setattr(spot.requests, "get", lambda url, timeout=25: _Resp(text))
    df = spot.fetch_spot("A", ["002739"])
    assert df.iloc[0]["pe_ttm"] == -81.20


def test_fetch_spot_hk_float_approximates_total(monkeypatch):
    # 港股：f[44]==f[45]=总市值，流通以总市值近似（IWF 恒为 1，已知限制）
    text = _qt_line("hk00700", "腾讯控股", 442.4, fm_yi=40272, tm_yi=40272)
    monkeypatch.setattr(spot.requests, "get", lambda url, timeout=25: _Resp(text))
    df = spot.fetch_spot("HK", ["00700"])
    row = df.iloc[0]
    assert row["total_mcap_local"] == 40272 * 1e8
    assert row["float_mcap_local"] == 40272 * 1e8


def test_fetch_spot_filters_invalid_rows(monkeypatch):
    text = (
        _qt_line("sh600519", "正常", 10.0, fm_yi=50, tm_yi=100)
        + _qt_line("sz000002", "停牌", "-", fm_yi=50, tm_yi=100)      # 价格无效
        + _qt_line("sh600036", "零市值", 10.0, fm_yi=0, tm_yi=0)      # 市值无效
        + _qt_line("sh601318", "字段不足", 10.0, fm_yi=50, tm_yi=100, n_fields=40)  # 行太短
    )
    monkeypatch.setattr(spot.requests, "get", lambda url, timeout=25: _Resp(text))
    df = spot.fetch_spot("A", ["600519", "000002", "600036", "601318"])
    assert list(df["code"]) == ["600519"]


def test_fetch_spot_failure_returns_none(monkeypatch):
    def _boom(url, timeout=25):
        raise ConnectionError("network down")

    monkeypatch.setattr(spot.requests, "get", _boom)
    assert spot.fetch_spot("A", ["600519"]) is None


def test_fetch_spot_unknown_market_raises():
    with pytest.raises(ValueError, match="unknown market"):
        spot.fetch_spot("XX", ["600519"])
