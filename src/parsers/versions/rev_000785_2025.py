"""
营收专用解析器 v4 — 000785(居然之家) 版式：收入/成本/毛利率表 + 占比表(候选1)

坑：候选0是收入/成本/毛利率表(无占比列)，候选1是占比表(有占比列+金额列)。
法：先按维度标记把行切桶，再在每个桶内对%求和≈100判占比列。
     金额列=占比列左侧最近大额数字列(同年块)；
     名称列=行首文本；按 分行业/分产品/分地区/分销售模式 切桶。
     把任何『分X』标记出现之前的数据行(有金额+占比的行)默认归到 industries。
认表：靠 分X 切桶标记 + 有占比列(桶内%求和≈100)。
"""
from src.parsers.infra.table_scanner import parse_money, parse_ratio, is_total_row

_MARK = {"分行业": "industries", "分产品": "segments", "分地区": "regions",
         "分销售模式": "by_channel", "分销售渠道": "by_channel"}


def _first_text(row):
    for c in row:
        if c and any("一" <= ch <= "鿿" for ch in str(c)):
            return str(c).replace("\n", "").strip()
    return ""


def _find_ratio_col_by_bucket(grid, ncols):
    """按维度标记切桶，在每个桶内对%求和≈100判占比列"""
    # 先找出所有维度标记行
    bucket_starts = []
    for i, row in enumerate(grid):
        for c in row:
            if (c or "").strip() in _MARK:
                bucket_starts.append(i)
                break
    
    if not bucket_starts:
        return None
    
    # 对每列，检查每个桶内的%求和是否≈100
    for c in range(ncols):
        all_buckets_ok = True
        for b_idx, start in enumerate(bucket_starts):
            end = bucket_starts[b_idx + 1] if b_idx + 1 < len(bucket_starts) else len(grid)
            bucket_vals = []
            for i in range(start + 1, end):
                row = grid[i]
                if c < len(row) and row[c] and "%" in str(row[c]):
                    v = parse_ratio(row[c])
                    if v is not None and 0 <= v <= 100:
                        bucket_vals.append(v)
            if len(bucket_vals) >= 2:
                s = sum(bucket_vals)
                if abs(s - 100) > 10:  # 桶内求和不在[90,110]
                    all_buckets_ok = False
                    break
            elif len(bucket_vals) == 1:
                if abs(bucket_vals[0] - 100) > 10:
                    all_buckets_ok = False
                    break
            # 桶内无%值，跳过
        if all_buckets_ok and bucket_starts:
            # 至少有一个桶有%值
            has_val = False
            for b_idx, start in enumerate(bucket_starts):
                end = bucket_starts[b_idx + 1] if b_idx + 1 < len(bucket_starts) else len(grid)
                for i in range(start + 1, end):
                    if c < len(grid[i]) and grid[i][c] and "%" in str(grid[i][c]):
                        has_val = True
                        break
                if has_val:
                    break
            if has_val:
                return c
    return None


def parse(tables, context=None):
    for t in tables:
        grid = t.get("table") or []
        if not grid:
            continue
        flat = "".join((c or "") for row in grid for c in row)
        if not any(m in flat for m in ("分产品", "分行业", "分地区")) or "%" not in flat:
            continue
        ncols = max((len(r) for r in grid), default=0)
        rc = _find_ratio_col_by_bucket(grid, ncols)
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
        cur = "industries"  # 默认归到 industries
        first_mark_found = False
        for row in grid:
            mark = next((_MARK[(c or "").strip()] for c in row if (c or "").strip() in _MARK), None)
            if mark:
                cur = mark
                first_mark_found = True
                continue
            name = _first_text(row)
            if not name:
                continue
            if is_total_row(name):
                continue
            rat = parse_ratio(row[rc]) if rc < len(row) and row[rc] and "%" in str(row[rc]) else None
            amt = parse_money(row[mc]) if mc < len(row) else None
            if rat is None and amt is None:
                continue
            if rat is not None and not (0 <= rat <= 100):
                rat = None
            # 如果还没遇到任何分X标记，归到industries
            if not first_mark_found:
                cur = "industries"
            out[cur].append({"name": name[:30], "revenue_yuan": amt, "ratio_pct": rat})
        if any(out.values()):
            return out
    return {"industries": [], "segments": [], "regions": [], "by_channel": []}