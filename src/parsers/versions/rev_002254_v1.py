"""
营收专用解析器 v1 — 002254(泰和新材) 版式：两年并排占比构成表

坑：表头(2025年/2024年)与数据列错位，通用解析器抓成了上期(2024)。
法(数据驱动，对错位免疫)：占比列=最左的 0~100% 列(本期在左)；金额列=占比列左侧最近大额列(同年块配对)；
     名称列=行首文本；按 分行业/分产品/分地区/分销售模式 切桶。
认表：靠 分X 切桶标记 + 有百分比列(表头"占营业收入比重"常被拆开，不靠它)。
"""
from src.parsers.infra.table_scanner import parse_money, parse_ratio, is_total_row

_MARK = {"分行业": "industries", "分产品": "segments", "分地区": "regions",
         "分销售模式": "by_channel", "分销售渠道": "by_channel"}


def _first_text(row):
    for c in row:
        if c and any("一" <= ch <= "鿿" for ch in str(c)):
            return str(c).replace("\n", "").strip()
    return ""


def parse(tables, context=None):
    for t in tables:
        grid = t.get("table") or []
        flat = "".join((c or "") for row in grid for c in row)
        if not any(m in flat for m in ("分产品", "分行业", "分地区")) or "%" not in flat:
            continue
        ncols = max((len(r) for r in grid), default=0)
        # 占比列：最左 0~100% 列(本期在左)
        rc = None
        for c in range(ncols):
            vals = [parse_ratio(row[c]) for row in grid
                    if c < len(row) and row[c] and "%" in str(row[c])]
            vals = [v for v in vals if v is not None and 0 <= v <= 100]
            if len(vals) >= 2:
                rc = c
                break
        if rc is None:
            continue
        # 金额列：占比列左侧最近大额数字列(同年块)
        mc = None
        for c in range(rc - 1, -1, -1):
            if sum(1 for row in grid if c < len(row) and (parse_money(row[c]) or 0) > 10000) >= 2:
                mc = c
                break
        if mc is None:
            continue

        out = {"industries": [], "segments": [], "regions": [], "by_channel": []}
        cur = "segments"
        for row in grid:
            mark = next((_MARK[(c or "").strip()] for c in row if (c or "").strip() in _MARK), None)
            if mark:
                cur = mark
                continue
            name = _first_text(row)
            rat = parse_ratio(row[rc]) if rc < len(row) and row[rc] and "%" in str(row[rc]) else None
            amt = parse_money(row[mc]) if mc < len(row) else None
            if not name or is_total_row(name) or (rat is None and amt is None):
                continue
            if rat is not None and not (0 <= rat <= 100):
                rat = None
            out[cur].append({"name": name[:30], "revenue_yuan": amt, "ratio_pct": rat})
        if any(out.values()):
            return out
    return {"industries": [], "segments": [], "regions": [], "by_channel": []}
