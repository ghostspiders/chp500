"""测试公共夹具：标准快照构造器与通用测试配置。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

AS_OF = pd.Timestamp("2026-08-15")

# 与 config.yaml 方法论参数一致的显式测试配置（避免依赖全局单例）
SCREEN_CFG = {
    "target_count": 500,
    "mcap_min": 40_000_000_000,
    "iwf_min": 0.20,
    "iwf_delist_threshold": 0.50,
    "liquidity_ratio_min": 1.0,
    "liquidity_ratio_min_by_market": {"A": 0.02, "HK": 0.30, "US": 0.30},
    "listing_min_months": 12,
}

# 一行"全项通过"的默认快照（22 列标准 schema）
DEFAULT_ROW = {
    "entity_id": "A.600000",
    "code": "600000",
    "name": "测试银行",
    "market": "A",
    "curr": "CNY",
    "is_st": False,
    "is_china": True,
    "price": 10.0,
    "total_shares": 1.0e10,
    "iwf": 0.50,
    "float_shares": 5.0e9,
    "total_mcap_local": 1.0e11,
    "float_mcap_local": 5.0e10,
    "total_mcap": 1.0e11,
    "float_mcap": 5.0e10,
    "ttm_net_profit": 1.0e10,
    "latest_q_net_profit": 2.0e9,
    "industry": "银行",
    "sector": "金融",
    "liquidity_ratio": 0.50,
    "listing_date": "2010-01-01",
}


def make_snapshot(overrides: list[dict] | None = None) -> pd.DataFrame:
    """构造快照表；overrides 中每个 dict 覆盖默认行（entity_id 自动去重）。"""
    rows = []
    for i, ov in enumerate(overrides or [{}]):
        row = dict(DEFAULT_ROW)
        row.update(ov)
        if not ov.get("entity_id"):
            row["entity_id"] = f"A.60000{i}"
            row["code"] = f"60000{i}"
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def snapshot() -> pd.DataFrame:
    return make_snapshot()


@pytest.fixture
def screen_cfg() -> dict:
    return dict(SCREEN_CFG)
