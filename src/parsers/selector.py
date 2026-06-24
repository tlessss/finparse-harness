"""
解析器选择器 — 自动选择最适合某个 PDF 的解析器

扫描对应目录下所有 py 文件，对每个解析器调用 can_handle()，
选置信度最高的那个。
"""

import os
import importlib
import inspect
from typing import Dict, Type, List
from pathlib import Path

from src.parsers.base import BaseParser


# 解析器目录映射（字段名 → 目录路径）
_PARSER_DIRS = {
    "revenue_breakdown": "revenue",
    "rnd_info": "rnd",
    "employees": "employee",
    "cost_breakdown": "cost",
    "top_clients": "top_supplier",
    "top_suppliers": "top_supplier",
}


def _discover_parsers(parser_dir: str) -> List[Type[BaseParser]]:
    """扫描目录下所有解析器类"""
    parsers = []
    dir_path = Path(__file__).parent / parser_dir
    if not dir_path.exists():
        return parsers

    for f in sorted(dir_path.glob("*.py")):
        if f.name == "__init__.py":
            continue
        module_name = f"src.parsers.{parser_dir}.{f.stem}"
        try:
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and
                    issubclass(obj, BaseParser) and
                    obj is not BaseParser and
                    hasattr(obj, "parse")):
                    parsers.append(obj)
        except Exception:
            continue

    return parsers


def select_parser(field: str, pdf_path: str, hint: str = "") -> Type[BaseParser]:
    """
    为指定字段选择最适合的解析器。

    Args:
        field: 字段名（revenue_breakdown / rnd_info / employees / cost_breakdown / top_clients）
        pdf_path: PDF 文件路径
        hint: 可选的提示信息（如公司行业）

    Returns:
        解析器类
    """
    parser_dir = _PARSER_DIRS.get(field, field)
    candidates = _discover_parsers(parser_dir)

    if not candidates:
        raise ImportError(f"未找到 {field} 的解析器")

    if len(candidates) == 1:
        return candidates[0]

    # 对所有候选解析器评分，选最高分
    best_cls = candidates[0]
    best_score = -1

    for cls in candidates:
        try:
            score = cls.can_handle(pdf_path, hint=hint)
            if score > best_score:
                best_score = score
                best_cls = cls
        except Exception:
            continue

    return best_cls


def list_available(field: str = None) -> Dict[str, List[str]]:
    """列出所有可用解析器"""
    result = {}
    for field_name, dir_name in _PARSER_DIRS.items():
        if field and field_name != field:
            continue
        cls_list = _discover_parsers(dir_name)
        result[field_name] = [f"{c.__module__}.{c.__name__}" for c in cls_list]
    return result
