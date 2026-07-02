"""解析结果 Context Pack：维度分项和、缺失维度、解析值 JSON。"""

import json
from typing import Any, Dict, List, Optional

REVENUE_EXPECTED_DIMS = ["industries", "segments", "regions", "by_channel"]


def dims_summary_text(dims: List[Dict], anchor: Optional[float] = None) -> str:
    parts = []
    for d in dims or []:
        s = d.get("sum") or 0
        tag = "过锚" if d.get("match") else "✗"
        parts.append(f"{d.get('dim')}={s / 1e8:.0f}亿({tag})")
    return "  ".join(parts) if parts else "无"


def anchor_summary_text(anchor: Optional[float]) -> str:
    return f"{anchor / 1e8:.2f}亿" if anchor else "无锚"


def missing_dims(value: Any, expected: List[str] = None) -> List[str]:
    expected = expected or REVENUE_EXPECTED_DIMS
    if not isinstance(value, dict):
        return list(expected)
    return [d for d in expected if not value.get(d)]


def parse_value_json(value: Any, max_len: int = 3500) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)[:max_len]


def parse_field_value(code: str, year: int, field: str, tables: list, pdf_path: Optional[str]) -> Any:
    """跑冷启动解析器拿当前字段值。"""
    if not pdf_path or not tables:
        return None
    try:
        from src.engine_orchestrator import FinParseAI
        parser = FinParseAI()._get_parser(field, pdf_path)
        return parser.parse(pdf_path, pre_scan=tables, code=code, year=year).get(field)
    except Exception:
        return None
