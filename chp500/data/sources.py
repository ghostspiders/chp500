"""数据源注册表（单一事实来源：config.yaml 的 data_sources 段）。

每个数据源一个配置条目，罗列其接口/调用 API、覆盖字段、市场与不可达策略。
本模块负责加载与校验注册表，并为直连源（transport=http）提供 URL 读取：
base_url / urls.* 均可在 config.yaml 中改址覆盖，代码内默认值仅作回退。
akshare/local 源的条目为文档化配置（实际调用函数在对应 module 中）。

REST 暴露：/api/sources（见 chp500/api/main.py）。
"""

from __future__ import annotations

from ..config import CONFIG

_TRANSPORTS = ("http", "akshare", "local")
_REQUIRED_FIELDS = ("name", "transport", "provides", "markets", "endpoints")


def data_sources() -> dict:
    """返回完整数据源注册表（浅拷贝；键即源标识）。"""
    return dict(CONFIG.get("data_sources") or {})


def get_source(key: str) -> dict:
    """取单个数据源条目；未配置时抛 KeyError。"""
    srcs = data_sources()
    if key not in srcs:
        raise KeyError(f"未配置的数据源: {key!r}（可用: {sorted(srcs)}）")
    return srcs[key]


def source_url(key: str, default: str) -> str:
    """读取直连源的 base_url（未配置或为空时回退 default）。"""
    return str(get_source(key).get("base_url") or default)


def source_urls(key: str, defaults: dict[str, str]) -> dict[str, str]:
    """批量读取直连源的附加 URL（urls 段；缺失项回退 defaults 对应值）。"""
    urls = get_source(key).get("urls") or {}
    return {k: str(urls.get(k) or v) for k, v in defaults.items()}


def validate() -> list[str]:
    """校验注册表完整性，返回问题列表（空列表 = 通过）。

    在加载期调用可尽早发现配置笔误（缺字段/未知 transport/空 endpoints）。
    """
    problems: list[str] = []
    for key, src in data_sources().items():
        if not isinstance(src, dict):
            problems.append(f"{key}: 条目须为映射")
            continue
        for f in _REQUIRED_FIELDS:
            if not src.get(f):
                problems.append(f"{key}: 缺少 {f}")
        if src.get("transport") not in _TRANSPORTS:
            problems.append(f"{key}: 未知 transport={src.get('transport')!r}（允许 {_TRANSPORTS}）")
        if src.get("transport") == "http" and not src.get("base_url"):
            problems.append(f"{key}: http 源必须提供 base_url")
    return problems
