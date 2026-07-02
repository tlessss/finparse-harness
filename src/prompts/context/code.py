"""代码/配置 Context Pack：revenue.yaml + 解析器认列/切桶源码片段。"""

import inspect
from typing import Any, Optional


def load_revenue_yaml(path: str = "src/parser_rules/revenue.yaml") -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "(读不到 revenue.yaml)"


def parser_source_snippets(parser: Any, methods=("_detect_columns", "_resolve_columns", "_classify")) -> str:
    """抽取解析器关键方法源码（认列/切桶）。"""
    code_src = ""
    for m in methods:
        fn = getattr(parser, m, None)
        if fn:
            try:
                code_src += inspect.getsource(fn) + "\n"
            except Exception:
                pass
    return code_src[:4000]
