"""行业分类（中文行业 -> GICS 风格 11 板块）与配比。

A 股行业来自雪球 affiliate_industry（如"白酒"/"银行"），港股/美股行业来自雪球
个股基本信息；此处用关键词映射到更粗的 GICS 风格板块，便于跨市场统一与行业平衡。
"""

from __future__ import annotations

import pandas as pd

from ..config import CONFIG

# 关键词 -> 板块（按顺序匹配，先到先得）
_SECTOR_KEYWORDS: list[tuple[str, list[str]]] = [
    ("金融", ["银行", "证券", "保险", "多元金融", "信托", "期货"]),
    ("能源", ["石油", "天然气", "煤炭", "油气", "炼化"]),
    ("原材料", ["化学", "化工", "钢铁", "钢", "有色", "金属", "建材", "材料", "造纸", "橡胶", "塑料"]),
    ("可选消费", ["汽车", "乘用车", "家电", "零售", "商贸", "旅游", "服饰", "传媒", "家居", "文娱", "酒店"]),
    ("工业", ["机械", "设备", "电力设备", "电气", "电池", "航空装备", "装备", "建筑", "工程", "军工", "航天", "运输", "物流", "航运", "铁路", "通用设备"]),
    ("信息技术", ["半导体", "电子", "软件", "计算机", "通信", "光学", "消费电子", "互联网", "数据", "芯片", "自动化"]),
    ("医疗保健", ["医疗", "医药", "生物", "制药", "健康", "基因", "疫苗", "中药"]),
    ("必需消费", ["食品", "饮料", "酒", "农业", "养殖", "乳业", "粮油", "超市"]),
    ("通信服务", ["电信", "通信服务", "广电", "网络"]),
    ("公用事业", ["电力", "水务", "燃气", "环保", "供热", "供电"]),
    ("房地产", ["房地产", "地产", "园区", "物业"]),
]

_GICS_SECTORS = [s for s, _ in _SECTOR_KEYWORDS] + ["其他"]


def map_to_sector(industry: str) -> str:
    if not isinstance(industry, str) or not industry:
        return "其他"
    for sector, kws in _SECTOR_KEYWORDS:
        for kw in kws:
            if kw in industry:
                return sector
    return "其他"


def classify_hk_us_sector(name: str, industry: str | None = None) -> str:
    """港美行业兜底归类。

    优先用人工核定的 industry 列；缺失时退化为对公司名称做关键词映射
    （港美免费行情接口无干净 GICS 字段，见 README 已知限制）。A 股行业由雪球实时提供，
    不走此路径。
    """
    if isinstance(industry, str) and industry.strip():
        return map_to_sector(industry)
    if isinstance(name, str) and name.strip():
        return map_to_sector(name)
    return "其他"


def add_sector(df: pd.DataFrame, industry_col: str = "industry") -> pd.DataFrame:
    out = df.copy()
    out["sector"] = out[industry_col].map(map_to_sector)
    return out


def sector_weights(df: pd.DataFrame, weight_col: str = "weight") -> pd.DataFrame:
    """汇总各行业权重占比。"""
    g = df.groupby("sector")[weight_col].sum().sort_values(ascending=False)
    return g.rename("sector_weight").reset_index()


def allocate(
    df: pd.DataFrame, cfg: dict | None = None, weight_col: str = "float_mcap"
) -> pd.DataFrame:
    """行业配比：passive（被动锚定市场，不封顶）或 soft（超 max_sector_weight 标记）。

    返回带 weight（基础自由流通市值权重）与 sector_exceed（是否超软上限）的表。
    """
    cfg = CONFIG if cfg is None else cfg
    out = df.copy()
    total = out[weight_col].sum()
    out["weight"] = out[weight_col] / total if total > 0 else 0.0

    mode = cfg.get("sector_cap_mode", "passive")
    cap = cfg.get("max_sector_weight", 0.20)
    if mode == "soft":
        sw = out.groupby("sector")["weight"].transform("sum")
        out["sector_exceed"] = sw > cap
    else:
        out["sector_exceed"] = False
    return out
