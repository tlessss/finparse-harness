"""
前五大供应商专用解析器 v1 — 300009 版式（前五名供应商明细表，D类=B判据:明细占比和≈前五合计）

契约：parse(tables) -> {top_suppliers:[{name,amount_yuan,ratio_pct}], total_ratio_pct, total_amount_yuan}
法：同客户解析器，认表换"供应商名称"+"占年度采购总额"。
"""

from src.parsers.infra.table_scanner import parse_money, parse_ratio


def _is_text(s):
    return bool(s) and any("一" <= c <= "鿿" for c in str(s))


def parse(tables, context=None):
    for t in tables:
        grid = t.get("table") or []
        flat = "".join((c or "") for row in grid for c in row)
        if "供应商名称" not in flat or "占年度采购" not in flat:
            continue
        ncols = max((len(r) for r in grid), default=0)
        rc = max(range(ncols), key=lambda c: sum(
            1 for row in grid if c < len(row) and row[c] and "%" in str(row[c])))
        mc = max(range(ncols), key=lambda c: sum(
            1 for row in grid if c < len(row) and (parse_money(row[c]) or 0) > 10000))
        nc = max(range(ncols), key=lambda c: sum(
            1 for row in grid if c < len(row) and _is_text(row[c]) and "供应商名称" not in str(row[c])))

        sups, total_ratio, total_amt = [], None, None
        for row in grid:
            rat = parse_ratio(row[rc]) if (rc < len(row) and row[rc] and "%" in str(row[rc])) else None
            if rat is None:
                continue
            amt = parse_money(row[mc]) if mc < len(row) else None
            row_text = "".join((c or "") for c in row)
            if "合计" in row_text or "小计" in row_text:
                total_ratio, total_amt = rat, amt
                continue
            name = (row[nc] or "").replace("\n", "").strip() if nc < len(row) else ""
            if not name or "供应商名称" in name:
                continue
            sups.append({"name": name, "amount_yuan": amt, "ratio_pct": rat})
        if sups:
            return {"top_suppliers": sups, "total_ratio_pct": total_ratio,
                    "total_amount_yuan": total_amt}
    return {"top_suppliers": [], "total_ratio_pct": None}
