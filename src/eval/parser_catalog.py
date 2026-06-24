"""
母本目录 + 选母本（选择即验证，营收字段级）— fork 优先的基础

已认证的营收专用解析器登记在此。修复一份失败报告时，先用"选择即验证"在母本里
挑最像的（跑一遍对 golden 打分），据分决定 复用 / fork / 新建（见 code_generator.repair）。

注：这里用 golden 打分选母本——适用于"正在认证某失败报告"的构建场景。
生产运行时对无 golden 的新报告，选母本/路由用硬规则代理（见 parsers/registry.py）。
"""

import os
from typing import List, Dict, Tuple

from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import version_parse_fn
from src.eval.revenue_score import score_revenue

# 已认证的营收专用解析器（exact 通过的）。后续由认证流程/前端自动维护。
CERTIFIED: List[Dict] = [
    {"key": "000425-工程机械占比构成表", "path": "src/parsers/versions/rev_000425_v1.py"},
]


def pick_mother(code: str, year: int, golden_rb: Dict,
                catalog: List[Dict] = None) -> Tuple:
    """选择即验证：跑每个已认证解析器 → 对 golden 打分 → 返回 (最优path, 分, key)。"""
    catalog = catalog if catalog is not None else CERTIFIED
    if get_tables(code, year) is None:
        return (None, -1.0, None)
    best = (None, -1.0, None)
    for c in catalog:
        if not os.path.exists(c["path"]):
            continue
        try:
            rb = version_parse_fn(c["path"])(code, year)
            s = score_revenue(rb, golden_rb)["score"]
        except Exception:
            s = -1.0
        if s > best[1]:
            best = (c["path"], s, c["key"])
    return best
