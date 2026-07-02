"""流水线 Context Pack：选表元信息、跨页可疑信号。"""

from typing import Dict, List, Optional, Tuple

FIELD_SIG = {
    "revenue_breakdown": "revenue",
    "cost_breakdown": "cost",
    "rnd_info": "rnd",
    "employees": "employee",
    "top_clients": "client",
    "top_suppliers": "supplier",
}

CROSS_PAGE_HINT = "可疑:选中页靠近页底，且相邻页有同主题表，可能是跨页续表未拼接。"


def field_sig(field: str) -> str:
    return FIELD_SIG.get(field, "revenue")


def select_pick(tables: list, code: str, year: int, field: str) -> Optional[Dict]:
    if not tables:
        return None
    from src.parsers.infra.table_recall import select_table
    return select_table(tables, code, year, field_sig(field))


def pick_meta_text(pick: Optional[Dict]) -> str:
    if not pick:
        return "未选中目标表"
    from src.prompts.context.table import ncols
    table = pick.get("table") or []
    return (
        f"page={pick.get('page')} rows={len(table)} cols={ncols(table)} "
        f"via={pick.get('via')} amount_col={pick.get('amount_col')} anchor_rel={pick.get('anchor_rel')} "
        f"dim_count={pick.get('dim_count')} caption={(pick.get('caption') or '').strip()[:120]}"
    )


def cross_page_suspect(pick: Optional[Dict], neighbor_lines: List[str]) -> Tuple[bool, str]:
    """返回 (是否可疑, 提示文案)。"""
    if not neighbor_lines or not pick or not pick.get("table_bbox") or not pick.get("page_h"):
        return False, ""
    tbb = pick.get("table_bbox")
    if tbb and tbb[3] >= float(pick.get("page_h") or 0) - 90:
        return True, CROSS_PAGE_HINT
    return False, ""
