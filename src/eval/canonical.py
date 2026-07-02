"""
单一真源 — 一份报告全字段的"权威解析结果"，供审核台/裁判/分诊/置信度共同读取
================================================================================

问题背景：以前各端点各自算字段值 —— 审核台走完整引擎(route-or-冷启动)、
裁判/分诊/置信度走 route_field —— 路由没命中时两者的值不同，导致
"审核台看到的数据 ≠ 裁判判的数据"。

本模块把它收敛成**一份缓存的权威结果**：
  get_canonical(code, year) → { field: {value, provenance, status, signal, source} }
  - value      : 展示/被判的值。统一 = 引擎结果(route 命中用路由值，否则冷启动兜底)，
                 即"审核台看到的那份"。裁判就判它、置信度就基于它，保证一致。
  - status     : route_field 的状态(routed / needs_repair)，供分诊判 reason。
  - signal     : 该值的硬规则信号 + 跨表锚置信度(clean/confidence/anchored/anchor)。
  - provenance : 溯源 {路径: {page,bbox}}。
  - source     : "routed"(专用解析器) / "cold_start"(通用兜底)。

抽表(scan_pdf)本就走 table_cache 复用；本结果再缓存到 goldset/canonical_cache。
"""

import json
import os
from typing import Dict, Optional

from src.eval.field_spec import FIELDS
from src.eval.table_cache import get_tables

_DIR = "goldset/canonical_cache"


def get_canonical(code: str, year: int, refresh: bool = False) -> Optional[Dict]:
    """一份报告的权威全字段结果(缓存)。无缓存表返回 None。"""
    os.makedirs(_DIR, exist_ok=True)
    path = os.path.join(_DIR, f"{code}_{year}.json")
    if os.path.exists(path) and not refresh:
        return json.load(open(path, encoding="utf-8"))
    if get_tables(code, year) is None:
        return None

    # 延迟导入避免环
    from src.console_service import _cached_engine_parse
    from src.parsers.revenue_router import route_field, field_plausibility
    from src.eval.anchors import get_anchors

    engine = _cached_engine_parse(code, year) or {}     # 值 + 溯源(route-or-冷启动)= 审核台真源
    anchors = get_anchors(code, year)
    prov_all = engine.get("溯源") or {}

    out: Dict[str, Dict] = {}
    for field, spec in FIELDS.items():
        rt = route_field(spec, code, year)              # 拿路由状态(缓存表上跑, ms)
        value = engine.get(field)                       # ★ 显示/被判的值统一用引擎值
        sig = rt.get("signal") or {}
        if not sig:                                     # 路由没信号(needs_repair)→ 按显示值自算
            sig = field_plausibility(spec, value, anchors=anchors) if value else {"clean": False}
        out[field] = {
            "value": value,
            "provenance": prov_all.get(field) or {},
            "status": rt.get("status"),
            "signal": sig,
            "source": "routed" if rt.get("status") == "routed" else "cold_start",
        }

    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def get_field(code: str, year: int, field: str, refresh: bool = False) -> Optional[Dict]:
    """取某字段的权威记录 {value, provenance, status, signal, source}；无则 None。"""
    c = get_canonical(code, year, refresh)
    return c.get(field) if c else None


def invalidate(code: str, year: int) -> None:
    """认证/改代码后作废该报告的权威缓存，下次重算。"""
    p = os.path.join(_DIR, f"{code}_{year}.json")
    if os.path.exists(p):
        os.remove(p)
