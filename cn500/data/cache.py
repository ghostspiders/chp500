"""parquet 缓存层，避免重复请求 AkShare（其有访问限速）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import CONFIG


class Cache:
    def __init__(self, cache_dir: str | Path | None = None):
        self.cache_dir = Path(cache_dir or CONFIG["cache_dir"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # 用 key 直接做文件名；调用方保证 key 不含路径分隔符
        return self.cache_dir / f"{key}.parquet"

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def get(self, key: str) -> pd.DataFrame | None:
        p = self._path(key)
        if p.exists():
            return pd.read_parquet(p)
        return None

    def put(self, key: str, df: pd.DataFrame) -> pd.DataFrame:
        df.to_parquet(self._path(key), index=False)
        return df

    def get_or_fetch(self, key: str, fetcher, *args, **kwargs) -> pd.DataFrame | None:
        cached = self.get(key)
        if cached is not None:
            return cached
        df = fetcher(*args, **kwargs)
        if df is not None and not df.empty:
            self.put(key, df)
        return df
