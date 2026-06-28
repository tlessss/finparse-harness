"""
解析器选择器 — 给某字段挑一个"通用解析器类"(冷启动用)
========================================================

机制：每个字段有一个目录(如 revenue/)，里面放着若干解析器 .py。
选择器扫这个目录、把所有解析器类找出来，逐个调 can_handle(pdf) 打分，选分最高的。
这样同一字段可以有多套解析器(如营收的 default 通用版 + bank 银行版)，按 PDF 特征自动挑。

注意区分：
  · 本文件 select_parser = 冷启动的"通用解析器选型"(代码里固定的几个解析器)
  · revenue_router.route_field = "选择即验证"，跑的是注册表里 LLM/人 生成的"专用解析器"
"""

import os
import importlib
import inspect
from typing import Dict, Type, List
from pathlib import Path

from src.parsers.base import BaseParser


# 字段名 → 该字段解析器所在的子目录名
_PARSER_DIRS = {
    "revenue_breakdown": "revenue",
    "rnd_info": "rnd",
    "employees": "employee",
    "cost_breakdown": "cost",
    "top_clients": "top_supplier",      # 客户和供应商共用一个目录(同一个解析器出双字段)
    "top_suppliers": "top_supplier",
}


def _discover_parsers(parser_dir: str) -> List[Type[BaseParser]]:
    """扫描某子目录下所有解析器类(继承自 BaseParser 且有 parse 方法的)。"""
    parsers = []
    dir_path = Path(__file__).parent / parser_dir
    if not dir_path.exists():
        return parsers

    for f in sorted(dir_path.glob("*.py")):
        if f.name == "__init__.py":
            continue
        # 动态 import 这个模块，再用 inspect 把里面符合条件的类挑出来
        module_name = f"src.parsers.{parser_dir}.{f.stem}"
        try:
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and
                    issubclass(obj, BaseParser) and       # 是解析器
                    obj is not BaseParser and             # 不是基类本身
                    hasattr(obj, "parse")):               # 实现了 parse()
                    parsers.append(obj)
        except Exception:
            continue          # 单个模块导入失败不影响其它
    return parsers


def select_parser(field: str, pdf_path: str, hint: str = "") -> Type[BaseParser]:
    """
    为指定字段选最合适的解析器类。

    Args:
        field   : 字段名(revenue_breakdown / rnd_info / employees / cost_breakdown / top_clients ...)
        pdf_path: PDF 路径(传给 can_handle 让解析器自己判断"我适不适合解这份")
        hint    : 可选提示(如公司行业)

    Returns:
        分最高的解析器类(注意是"类"，不是实例)
    """
    parser_dir = _PARSER_DIRS.get(field, field)
    candidates = _discover_parsers(parser_dir)

    if not candidates:
        raise ImportError(f"未找到 {field} 的解析器")

    if len(candidates) == 1:
        return candidates[0]                  # 只有一个就直接用，省得评分

    # 多个候选 → 逐个 can_handle() 打分，选最高
    best_cls = candidates[0]
    best_score = -1
    for cls in candidates:
        try:
            score = cls.can_handle(pdf_path, hint=hint)    # 0~1 置信度，解析器自评
            if score > best_score:
                best_score = score
                best_cls = cls
        except Exception:
            continue
    return best_cls


def list_available(field: str = None) -> Dict[str, List[str]]:
    """列出所有(或某字段的)可用解析器，调试用。"""
    result = {}
    for field_name, dir_name in _PARSER_DIRS.items():
        if field and field_name != field:
            continue
        cls_list = _discover_parsers(dir_name)
        result[field_name] = [f"{c.__module__}.{c.__name__}" for c in cls_list]
    return result
