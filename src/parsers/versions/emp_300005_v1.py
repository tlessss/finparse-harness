"""
员工专用解析器 v1 — 300005 版式（员工情况表，C类:分项和=总数）

契约：parse(tables) -> {total, composition:[{name,count}], education:[{name,count}]}
法：选含"专业构成"+"教育程度"的表 → 按这两个标记把行切成两块 → 名称=行首文本、
    人数=行内首个整数 → "在职员工的数量合计"行取 total → 跳表头/合计行。
"""

from src.parsers.infra.table_scanner import parse_money, is_total_row


def _int(s):
    v = parse_money(s)
    return int(round(v)) if v is not None else None


def parse(tables, context=None):
    for t in tables:
        grid = t.get("table") or []
        flat = "".join((c or "") for row in grid for c in row)
        if "专业构成" not in flat or "教育程度" not in flat:
            continue
        total, comp, edu, cur = None, [], [], None
        for row in grid:
            cells = [(c or "").replace("\n", "").strip() for c in row]
            name = next((c for c in cells if c), "")
            count = next((_int(c) for c in cells if _int(c) is not None), None)
            if "在职员工的数量合计" in name:
                if count is not None:
                    total = count
                continue
            if "专业构成" in name:
                cur = comp
                continue
            if "教育程度" in name:
                cur = edu
                continue
            if not name or count is None or "合计" in name or is_total_row(name):
                continue
            if cur is not None:
                cur.append({"name": name, "count": count})
        if comp or edu:
            return {"total": total, "composition": comp, "education": edu}
    return {"total": None, "composition": [], "education": []}
