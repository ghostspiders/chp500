"""缓存层：避免重复请求 AkShare（其有访问限速）。

- DataFrame 值落 parquet（行情/业绩等表格数据）
- dict/list 值落 JSON（如 EDGAR 的 ticker->CIK 映射、companyconcept 响应）

支持 TTL：条目按文件 mtime 判断新鲜度，超过 `cache_ttl_days` 视为未命中并重取。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from ..config import CONFIG


def _is_empty(value) -> bool:
    if isinstance(value, pd.DataFrame):
        return value.empty
    return not value  # dict/list 为空


class Cache:
    def __init__(self, cache_dir: str | Path | None = None, ttl_days: float | None = None):
        self.cache_dir = Path(cache_dir or CONFIG["cache_dir"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if ttl_days is None:
            ttl_days = CONFIG.get("cache_ttl_days")
        self.ttl_seconds: float | None = float(ttl_days) * 86400 if ttl_days else None

    def _path(self, key: str) -> Path:
        # 用 key 直接做文件名；调用方保证 key 不含路径分隔符
        return self.cache_dir / f"{key}.parquet"

    def _json_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def exists(self, key: str) -> bool:
        return self._path(key).exists() or self._json_path(key).exists()

    def _is_fresh(self, path: Path) -> bool:
        if self.ttl_seconds is None:
            return True
        return (time.time() - path.stat().st_mtime) < self.ttl_seconds

    def get(self, key: str) -> pd.DataFrame | dict | list | None:
        p = self._path(key)
        if p.exists():
            if not self._is_fresh(p):
                return None
            return pd.read_parquet(p)
        jp = self._json_path(key)
        if jp.exists():
            if not self._is_fresh(jp):
                return None
            with open(jp, encoding="utf-8") as f:
                return json.load(f)
        return None

    def put(self, key: str, value: pd.DataFrame | dict | list):
        if isinstance(value, pd.DataFrame):
            value.to_parquet(self._path(key), index=False)
        else:
            with open(self._json_path(key), "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        return value

    def get_or_fetch(self, key: str, fetcher, *args, **kwargs):
        cached = self.get(key)
        if cached is not None:
            return cached
        value = fetcher(*args, **kwargs)
        if value is not None and not _is_empty(value):
            self.put(key, value)
        return value
