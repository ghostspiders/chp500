"""数据源注册表（sources）单元测试：加载、校验、URL 读取与回退。"""

from __future__ import annotations

import pytest

from chp500.data import sources

# 六个数据源：三市场行情/快照/净利/行业/汇率 + 本地参考表
EXPECTED_SOURCES = [
    "tencent_spot", "xueqiu_info", "sec_edgar", "sina_market", "boc_fx", "local_reference",
]


def test_registry_contains_all_sources():
    keys = set(sources.data_sources())
    missing = set(EXPECTED_SOURCES) - keys
    assert not missing, f"注册表缺少数据源: {missing}"


def test_validate_passes_on_repo_config():
    # 仓库自带 config.yaml 的 data_sources 必须完整无误
    assert sources.validate() == []


def test_each_source_has_required_fields():
    for key, src in sources.data_sources().items():
        assert src["name"], key
        assert src["transport"] in ("http", "akshare", "local"), key
        assert src["provides"], key
        assert src["markets"], key
        assert src["endpoints"], key
        assert src.get("on_failure"), key


def test_get_source_unknown_key_raises():
    with pytest.raises(KeyError, match="未配置的数据源"):
        sources.get_source("nonexistent")


def test_source_url_reads_config_with_fallback():
    # 配置中的 base_url 生效
    assert sources.source_url("tencent_spot", "http://default").startswith("https://qt.gtimg.cn")
    assert sources.source_url("sec_edgar", "http://default").startswith("https://data.sec.gov")
    # 未配置的键 -> KeyError（不得静默回退，避免拼错键名后打到错误地址）
    with pytest.raises(KeyError):
        sources.source_url("nonexistent", "http://default")


def test_source_urls_fills_missing_from_defaults():
    urls = sources.source_urls("sec_edgar", {
        "ticker_map": "http://default-map",
        "not_in_config": "http://default-extra",
    })
    assert urls["ticker_map"].startswith("https://www.sec.gov")  # 配置值
    assert urls["not_in_config"] == "http://default-extra"      # 缺失回退默认


def test_validate_catches_bad_entries(monkeypatch):
    bad = {
        "src_no_name": {"transport": "http", "provides": ["x"], "markets": ["A"],
                        "endpoints": [{"api": "GET /x"}], "base_url": "http://x"},
        "src_bad_transport": {"name": "x", "transport": "grpc", "provides": ["x"],
                              "markets": ["A"], "endpoints": [{"api": "GET /x"}]},
        "src_http_no_base": {"name": "x", "transport": "http", "provides": ["x"],
                             "markets": ["A"], "endpoints": [{"api": "GET /x"}]},
    }
    monkeypatch.setattr(sources, "data_sources", lambda: bad)
    problems = sources.validate()
    assert any("缺少 name" in p for p in problems)
    assert any("未知 transport" in p for p in problems)
    assert any("必须提供 base_url" in p for p in problems)
