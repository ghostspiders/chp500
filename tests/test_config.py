"""配置加载（config）单元测试。"""

from __future__ import annotations

from chp500.config import _DEFAULTS, load_config


def test_missing_file_falls_back_to_defaults(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg["target_count"] == _DEFAULTS["target_count"]
    assert cfg["mcap_min"] == _DEFAULTS["mcap_min"]


def test_user_yaml_overrides_defaults_shallowly(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("target_count: 300\ncache_ttl_days: 3\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["target_count"] == 300
    assert cfg["cache_ttl_days"] == 3
    assert cfg["mcap_min"] == _DEFAULTS["mcap_min"]  # 未覆盖项保留默认


def test_repo_config_yaml_loads_and_has_ttl():
    cfg = load_config()  # 仓库根 config.yaml
    assert cfg["cache_ttl_days"] == 7
    assert cfg["liquidity_ratio_min_by_market"]["A"] == 0.02
