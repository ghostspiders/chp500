"""缓存层（data.cache）单元测试：回环与 TTL 过期。"""

from __future__ import annotations

import os
import time

import pandas as pd
import pytest

from chp500.data.cache import Cache


def test_put_get_roundtrip(tmp_path):
    c = Cache(tmp_path / "cache")
    df = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
    c.put("k", df)
    got = c.get("k")
    assert got is not None
    pd.testing.assert_frame_equal(got, df)
    assert c.exists("k")
    assert c.get("missing") is None


def test_get_or_fetch_caches_result(tmp_path):
    c = Cache(tmp_path / "cache", ttl_days=None)
    calls = {"n": 0}

    def fetcher():
        calls["n"] += 1
        return pd.DataFrame({"x": [1]})

    r1 = c.get_or_fetch("k", fetcher)
    r2 = c.get_or_fetch("k", fetcher)
    assert calls["n"] == 1
    pd.testing.assert_frame_equal(r1, r2)


def test_ttl_expiry_triggers_refetch(tmp_path):
    c = Cache(tmp_path / "cache", ttl_days=7)
    c.put("k", pd.DataFrame({"x": [1]}))
    # 伪造写入时间为 10 天前 -> 过期
    old = time.time() - 10 * 86400
    os.utime(c._path("k"), (old, old))
    assert c.get("k") is None
    assert c.exists("k")  # 文件仍在，仅视为不新鲜

    calls = {"n": 0}

    def fetcher():
        calls["n"] += 1
        return pd.DataFrame({"x": [2]})

    r = c.get_or_fetch("k", fetcher)
    assert calls["n"] == 1
    assert r["x"].tolist() == [2]
    # 重写后恢复新鲜
    assert c.get("k") is not None


def test_ttl_none_falls_back_to_config_default(tmp_path):
    # ttl_days 未显式指定时回落到全局 CONFIG（仓库配置 cache_ttl_days=7）
    c = Cache(tmp_path / "cache", ttl_days=None)
    assert c.ttl_seconds == pytest.approx(7 * 86400)
    c.put("k", pd.DataFrame({"x": [1]}))
    old = time.time() - 10 * 86400
    os.utime(c._path("k"), (old, old))
    assert c.get("k") is None


def test_ttl_zero_means_never_expire(tmp_path):
    c = Cache(tmp_path / "cache", ttl_days=0)
    c.put("k", pd.DataFrame({"x": [1]}))
    old = time.time() - 3650 * 86400
    os.utime(c._path("k"), (old, old))
    assert c.get("k") is not None


def test_empty_fetch_not_cached(tmp_path):
    c = Cache(tmp_path / "cache", ttl_days=None)
    c.get_or_fetch("k", lambda: pd.DataFrame())
    assert not c.exists("k")
    c.get_or_fetch("none", lambda: None)
    assert not c.exists("none")
