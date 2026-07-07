from src.parsers.infra.table_scanner import is_total_row, parse_money, parse_ratio

_MARKERS = {"分行业": "industries", "分产品": "segments",
            "分地区": "regions", "分销售模式": "by_channel"}


def _is_pct(s) -> bool:
    if not s or "%" not in str(s):
        return False
    v = parse_ratio(s)
    return v is not None and 0 <= v <= 100


def _bucket(grid):
    """按 分X 标记切桶；标记前的数据行归 industries。返回 {dim: [row,...]}。"""
    buckets, cur = {}, "industries"
    for row in grid:
        if not row:
            continue
        name = (row[0] or "").replace(" ", "").replace("\n", "").strip()
        if name in _MARKERS:
            cur = _MARKERS[name]
            continue
        if is_total_row(name):
            continue
        if name and any(_is_pct(c) for c in row[1:]):   # 数据行：有%值
            buckets.setdefault(cur, []).append(row)
    return buckets


def _ratio_col(buckets):
    """在最大的桶里，找%-值求和≈100 的最左列（当年占比列）。"""
    rows = max(buckets.values(), key=len) if buckets else []
    ncols = max((len(r) for r in rows), default=0)
    for c in range(1, ncols):
        vals = [parse_ratio(r[c]) for r in rows if c < len(r) and _is_pct(r[c])]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 2 and 95 <= sum(vals) <= 105:
            return c
    return None


def _money_col_left_of(buckets, rc):
    """占比列左侧最近的"大额数字"列 = 当年金额列。"""
    rows = [r for rs in buckets.values() for r in rs]
    for c in range(rc - 1, -1, -1):
        hits = sum(1 for r in rows
                   if c < len(r) and (parse_money(r[c]) or 0) > 1000)
        if hits >= 2:
            return c
    return None


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else None


def parse(tables, context=None):
    for t in tables:
        grid = t.get("table") or []
        # 只认带"分产品/分地区"维度标记的营收构成表
        flat = "".join((c or "") for row in grid for c in row)
        if "分产品" not in flat and "分地区" not in flat:
            continue
        buckets = _bucket(grid)
        rc = _ratio_col(buckets)
        if rc is None:
            continue                       # 桶内无≈100列 → 多半是毛利率表，跳过
        mc = _money_col_left_of(buckets, rc)
        out = {}
        for dim, rows in buckets.items():
            items = []
            for row in rows:
                name = (row[0] or "").replace("\n", " ").strip()
                rat = parse_ratio(_cell(row, rc))
                if not name or rat is None:
                    continue
                items.append({"name": name,
                              "revenue_yuan": parse_money(_cell(row, mc)),
                              "ratio_pct": rat})
            if items:
                out[dim] = items
        if out.get("segments") or out.get("industries") or out.get("regions"):
            return out
    return {}