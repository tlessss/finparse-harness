"""默认抽表器 —— 现有启发式 filter_by_signature。专用抽表器没覆盖时的兜底。

注意：这里**不加**任何针对单个报告的硬编码特征(那是"掩盖症状")。默认器保持通用启发式；
个别版式漏表 → 走流程发**专用抽表器**(finders/versions/)，而不是往默认器塞关键词。
"""

from src.parsers.infra.table_scanner import filter_by_signature

_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd",
        "employees": "employee", "top_clients": "supplier", "top_suppliers": "supplier"}


def find(tables, field, context=None):
    """返回该字段的候选表(按启发式得分降序)。"""
    return filter_by_signature(tables or [], _SIG.get(field, "revenue"))
