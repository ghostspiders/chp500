"""配置加载：config.yaml + .env。"""

from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "target_count": 500,
    "base_currency": "CNY",
    "mcap_min": 40_0000_0000,
    "iwf_min": 0.20,
    "iwf_delist_threshold": 0.50,
    "liquidity_ratio_min": 1.0,
    "liquidity_ratio_min_by_market": {"A": 0.02, "HK": 0.30, "US": 0.30},
    "listing_min_months": 12,
    "require_4q_positive": True,
    "sector_scheme": "GICS",
    "sector_cap_mode": "passive",
    "max_sector_weight": 0.20,
    "single_cap_mode": "monitored",
    "cap_single": 0.10,
    "convergence_tol": 1e-6,
    "rebalance_freq": "quarterly",
    "iwf_refresh": "weekly",
    "buffer_low": 400,
    "buffer_high": 550,
    "fast_entry_rank": 50,
    "committee_discretion": True,
    "include_bse": False,
    "markets": ["A", "HK", "US"],
    "data_source": "akshare",
    "cache_dir": ".cache",
    "cache_ttl_days": 7,
}


def load_config(path=None) -> dict:
    """加载配置，缺失项回退到 _DEFAULTS。"""
    load_dotenv(BASE_DIR / ".env")
    cfg_path = Path(path) if path else BASE_DIR / "config.yaml"
    user_cfg: dict = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
    merged = dict(_DEFAULTS)
    merged.update(user_cfg)
    return merged


# 全局配置单例
CONFIG = load_config()
