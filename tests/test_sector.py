"""行业分类（sector.classifier）单元测试。"""

from __future__ import annotations

import pandas as pd

from chp500.sector.classifier import add_sector, allocate, map_to_sector


def test_keyword_mapping():
    cases = {
        "银行": "金融",
        "证券": "金融",
        "煤炭开采": "能源",
        "石油与天然气的开采": "能源",
        "钢铁": "原材料",
        "有色金属": "原材料",
        "汽车服务": "可选消费",
        "旅游零售": "可选消费",
        "半导体": "信息技术",
        "软件开发": "信息技术",
        "医疗器械": "医疗保健",
        "白酒": "必需消费",
        "食品加工": "必需消费",
        "电信运营": "通信服务",
        "电力": "公用事业",
        "房地产": "房地产",
        "物业管理": "房地产",
    }
    for industry, sector in cases.items():
        assert map_to_sector(industry) == sector, industry


def test_industry_priority_order():
    # "电力设备" 命中工业（列表顺序在公用事业之前），而非公用事业的"电力"
    assert map_to_sector("电力设备") == "工业"
    assert map_to_sector("电力") == "公用事业"


def test_fallback_other():
    assert map_to_sector("") == "其他"
    assert map_to_sector(None) == "其他"
    assert map_to_sector("未知行业") == "其他"


def test_add_sector_column():
    df = pd.DataFrame({"industry": ["银行", "白酒", "奇怪行业"]})
    out = add_sector(df)
    assert list(out["sector"]) == ["金融", "必需消费", "其他"]


def test_allocate_passive_no_exceed_flag():
    df = pd.DataFrame({
        "sector": ["金融", "金融", "信息技术"],
        "float_mcap": [5.0e11, 4.0e11, 1.0e11],
    })
    out = allocate(df, cfg={"sector_cap_mode": "passive", "max_sector_weight": 0.20})
    assert not out["sector_exceed"].any()
    assert out["weight"].sum() == 1.0 and (out["weight"] >= 0).all()


def test_allocate_soft_flags_oversized_sector_members():
    df = pd.DataFrame({
        "sector": ["金融", "金融", "信息技术"],
        "float_mcap": [5.0e11, 4.0e11, 1.0e11],  # 金融合计 90%
    })
    out = allocate(df, cfg={"sector_cap_mode": "soft", "max_sector_weight": 0.20})
    assert out.loc[out["sector"] == "金融", "sector_exceed"].all()
    assert not out.loc[out["sector"] == "信息技术", "sector_exceed"].any()


def test_allocate_defaults_to_global_config():
    # cfg=None 时应回落到全局 CONFIG（passive），而非空配置崩溃
    df = pd.DataFrame({"sector": ["金融"], "float_mcap": [1.0e11]})
    out = allocate(df)
    assert "sector_exceed" in out.columns
    assert not out["sector_exceed"].any()
