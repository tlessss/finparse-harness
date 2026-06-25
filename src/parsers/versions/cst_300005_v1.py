"""
成本专用解析器 v1 — 300005 版式（成本构成"占营业成本比重"表，扁平列表）

契约：parse(tables) -> cost_breakdown 列表 [{name, amount_yuan, ratio_pct}]
法：选含"占营业成本比重"的表 → 找"%-值求和≈100 的最左列"当占比列(避同比/去年)
     → 金额列=占比列左侧最近大额数字列 → 名称列=金额列左侧最近文本列(成本构成项)
"""

from src.parsers.infra.table_scanner import parse_money, parse_ratio, is_total_row


def _is_text(s):
    return bool(s) and any("一" <= c <= "鿿" for c in str(s))


def _is_pct(s):
    if not s or "%" not in str(s):
        return False
    v = parse_ratio(s)
    return v is not None and 0 <= v <= 100


def parse(tables, context=None):
    for t in tables:
        grid = t.get("table") or []
        flat = "".join((c or "") for row in grid for c in row)
        if "占营业成本比重" not in flat and "占成本比重" not in flat:
            continue
        ncols = max((len(r) for r in grid), default=0)

        # 占比列：%-值求和≈100 的最左列（当年）
        rc = None
        for c in range(ncols):
            vals = [parse_ratio(row[c]) for row in grid
                    if c < len(row) and _is_pct(row[c])]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2 and 95 <= sum(vals) <= 105:
                rc = c
                break
        if rc is None:
            continue
        # 金额列：占比列左侧最近的大额数字列
        mc = None
        for c in range(rc - 1, -1, -1):
            if sum(1 for row in grid if c < len(row) and (parse_money(row[c]) or 0) > 1000) >= 2:
                mc = c
                break
        if mc is None:
            continue
        # 名称列：金额列左侧最近的文本列（成本构成项）
        nc = 0
        for c in range(mc - 1, -1, -1):
            if sum(1 for row in grid if c < len(row) and _is_text(row[c])) >= 2:
                nc = c
                break

        out = []
        for row in grid:
            name = (row[nc] or "").replace("\n", "").strip() if nc < len(row) else ""
            rat = parse_ratio(row[rc]) if rc < len(row) else None
            if not name or rat is None or is_total_row(name):
                continue
            out.append({"name": name,
                        "amount_yuan": parse_money(row[mc]) if mc < len(row) else None,
                        "ratio_pct": rat})
        if out:
            return out
    return []
