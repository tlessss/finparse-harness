"""
事后自动溯源 — 给"只吐值"的版本解析器白补溯源

版本解析器(parse(tables)->revenue_breakdown)只返回值，不带出处。本模块拿这些值
回到带 cell_bbox 的源表里匹配：先按名称定位到那一行，再在行内按值找到收入/占比格，
反查出 (page, bbox)。任何版本解析器(含 LLM 写的)都白得溯源，契约不用改。

输出键格式与 M1/show_provenance 一致：'{dim}[{i}].name|revenue_yuan|ratio_pct'。
"""

from typing import Dict, List

from src.parsers.infra.table_scanner import parse_money, parse_ratio


def _norm(s) -> str:
    return "".join(ch for ch in str(s or "") if ch not in " 　\t\n、,，()（）")


def _rows(tables):
    """逐行产出 (page, grid_row, bbox_row)。"""
    for t in tables or []:
        grid = t.get("table") or []
        bb = t.get("cell_bbox") or []
        page = t.get("page")
        for r in range(len(grid)):
            yield page, grid[r], (bb[r] if r < len(bb) else [])


def attach_provenance(rb: Dict, tables: List[Dict]) -> Dict:
    """给一份 revenue_breakdown 反查溯源，返回 {字段路径: {page, bbox}}。"""
    prov: Dict = {}
    for dim, rows in (rb or {}).items():
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows):
            name = row.get("name")
            rev = row.get("revenue_yuan")
            rat = row.get("ratio_pct")
            nn = _norm(name)
            if not nn:
                continue
            # 1) 按名称定位到源表那一行
            target = None
            for page, grow, brow in _rows(tables):
                for c in range(len(grow)):
                    if grow[c] and _norm(grow[c]) == nn:
                        target = (page, grow, brow, c)
                        break
                if target:
                    break
            if not target:
                continue
            page, grow, brow, name_c = target

            def bb(c):
                return brow[c] if (c is not None and 0 <= c < len(brow)) else None

            if bb(name_c):
                prov[f"{dim}[{i}].name"] = {"page": page, "bbox": bb(name_c)}
            # 2) 行内按值找收入格
            if rev is not None:
                for c in range(len(grow)):
                    m = parse_money(grow[c]) if grow[c] else None
                    if m is not None and abs(m - rev) <= max(1.0, abs(rev) * 1e-6):
                        if bb(c):
                            prov[f"{dim}[{i}].revenue_yuan"] = {"page": page, "bbox": bb(c)}
                        break
            # 3) 行内按值找占比格
            if rat is not None:
                for c in range(len(grow)):
                    rr = parse_ratio(grow[c]) if (grow[c] and "%" in str(grow[c])) else None
                    if rr is not None and abs(rr - rat) <= 0.01:
                        if bb(c):
                            prov[f"{dim}[{i}].ratio_pct"] = {"page": page, "bbox": bb(c)}
                        break
    return prov
