"""表相关 Context Pack：预览、邻近页、候选对照、选中表网格。"""

from typing import Dict, List, Optional


def ncols(grid: list) -> int:
    return max((len(r) for r in (grid or [])), default=0)


def table_preview(grid: list, max_rows: int = 35, max_cols: int = 10) -> str:
    out = []
    for row in (grid or [])[:max_rows]:
        cells = [((c or "").replace("\n", " ").strip())[:28] for c in row[:max_cols]]
        out.append(" | ".join(cells))
    return "\n".join(out)


def row_preview(grid: list, row_idx: int = 0, max_cols: int = 8) -> str:
    if not grid:
        return ""
    row = grid[row_idx] if 0 <= row_idx < len(grid) else []
    return " | ".join(((c or "").replace("\n", " ").strip())[:24] for c in row[:max_cols])


def neighbor_table_lines(tables: List[Dict], center_page: int, limit: int = 12) -> List[str]:
    """选中页 ±1 的表片段摘要（跨页续表排查）。"""
    if not center_page:
        return []
    nearby = [t for t in tables if isinstance(t.get("page"), int) and center_page - 1 <= t["page"] <= center_page + 1]
    nearby.sort(key=lambda x: (x.get("page", 0), x.get("table_bbox", (0, 0, 0, 0))[1] if x.get("table_bbox") else 0))
    lines = []
    for t in nearby[:limit]:
        g = t.get("table") or []
        tbb = t.get("table_bbox") or ()
        near_bottom = ""
        if tbb and t.get("page_h"):
            near_bottom = " near_bottom=Y" if tbb[3] >= float(t.get("page_h")) - 90 else ""
        lines.append(
            f"p{t.get('page')} rows={len(g)} cols={ncols(g)}{near_bottom} "
            f"cap={(t.get('caption') or '').strip()[:60]} "
            f"row0=[{row_preview(g, 0)}]"
        )
    return lines


def _reading_order(tables: List[Dict]) -> List[Dict]:
    """按阅读顺序排（页码，表在页内的纵向位置）。"""
    return sorted(
        [t for t in (tables or []) if isinstance(t.get("page"), int)],
        key=lambda t: (t.get("page", 0), (t.get("table_bbox") or (0, 0, 0, 0))[1]),
    )


def _pick_index(order: List[Dict], pick: Optional[Dict]) -> int:
    """在阅读序里定位选中表：先按表格对象身份，再按(页,bbox)兜底。找不到返回 -1。"""
    if not pick:
        return -1
    pgrid, pg, bb = pick.get("table"), pick.get("page"), pick.get("table_bbox")
    for i, t in enumerate(order):
        if pgrid is not None and t.get("table") is pgrid:
            return i
    for i, t in enumerate(order):
        if t.get("page") == pg and t.get("table_bbox") == bb:
            return i
    return -1


def next_table_content(tables: List[Dict], pick: Optional[Dict],
                       n: int = 1, max_rows: int = 25, max_cols: int = 20) -> List[str]:
    """选中表**之后紧接的下一张表**的实际内容（阅读序），用于判断跨页续表。
    只给下一张、且给整表网格而非一行摘要——避免把邻近页所有无关表都堆给 LLM。"""
    order = _reading_order(tables)
    idx = _pick_index(order, pick)
    if idx < 0:
        return []
    out = []
    for t in order[idx + 1: idx + 1 + n]:
        g = t.get("table") or []
        bb, page_h = t.get("table_bbox") or (), t.get("page_h")
        at_top = " top_of_page=Y" if (bb and page_h and bb[1] <= 120) else ""
        head = (f"p{t.get('page')} rows={len(g)} cols={ncols(g)}{at_top} "
                f"cap={(t.get('caption') or '').strip()[:80]}")
        out.append(head + "\n" + table_preview(g, max_rows=max_rows, max_cols=max_cols))
    return out


def candidate_table_lines(tables: List[Dict], code: str, year: int, sig: str, top_k: int = 6) -> List[str]:
    """向量召回 + 锚精判候选对照。"""
    try:
        from src.parsers.infra.table_recall import vector_recall, anchor_select, _dimension_count
        recalled = vector_recall(tables, sig, top_k=top_k, threshold=0.0)
        judged = anchor_select(recalled, code, year, sig)
        judged_by_id = {id(c.get("table")): c for c in (judged or [])}
        lines = []
        for i, t in enumerate(recalled[:top_k], start=1):
            j = judged_by_id.get(id(t.get("table")), {})
            lines.append(
                f"{i}. p{t.get('page')} rows={len(t.get('table') or [])} cols={ncols(t.get('table') or [])} "
                f"recall={t.get('recall_score')} anchor_rel={j.get('anchor_rel')} amount_col={j.get('amount_col')} "
                f"dim_count={_dimension_count(t.get('table') or []) if sig in ('revenue', 'cost') else '-'} "
                f"caption={(t.get('caption') or '').strip()[:80]}"
            )
        return lines
    except Exception:
        return []


def selected_table_grid(code: str, year: int, field_sig: str, max_rows: int = 45) -> str:
    """生产链路选中表 → 结构化网格文本（供 judge/verify 复用）。"""
    try:
        from src.eval.table_cache import get_tables
        from src.parsers.infra.table_recall import select_table
        tables = get_tables(code, year)
        if not tables:
            return ""
        sel = select_table(tables, code, year, field_sig)
        if not sel or not sel.get("table"):
            return ""
        return table_preview(sel["table"], max_rows=max_rows, max_cols=20)
    except Exception:
        return ""
