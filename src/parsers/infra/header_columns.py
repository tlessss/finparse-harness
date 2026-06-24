"""
表头驱动认列 — M1 核心组件

替代"纯统计认列"。思路：
  1. 用表头别名在前几行定位每个语义列的"表头列索引"
  2. 真实数据列 = 值类型匹配 且 离表头列最近的那一列
     （解决两个老问题：①认错列②两个年度的占比列选错——取离表头最近=当年列）

关键安全约束（正确率优先）：
  - 占比列(ratio)只有当表头命中"占营业收入比重"类别名才成立；
    若表头只有"毛利率"，ratio_col = None（绝不拿毛利率顶替）。

用法：
  from src.parsers.infra.header_columns import detect_columns_by_header
  cols = detect_columns_by_header(table, aliases)
  # → {"name": int|None, "revenue": int|None, "ratio": int|None, ...}
"""

from typing import Dict, List, Optional


def _is_money(s: str) -> bool:
    if not s:
        return False
    s = s.replace(",", "").replace("，", "").strip()
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_ratio(s: str) -> bool:
    if not s or "%" not in s:
        return False
    try:
        v = float(s.replace("%", "").replace(",", "").replace("（", "-").replace("(", "-")
                  .replace("）", "").replace(")", "").strip())
        return 0 <= abs(v) <= 100
    except ValueError:
        return False


def _is_text(s: str) -> bool:
    return bool(s) and any("一" <= c <= "鿿" for c in s)


# 每个语义列的值判定函数
_PREDICATES = {
    "name": _is_text,
    "revenue": _is_money,
    "ratio": _is_ratio,
    "cost": _is_money,
    "gross": _is_ratio,
}


def _column_headers(table: List[list], scan_rows: int = 3) -> Dict[int, str]:
    """把前 scan_rows 行按列拼成每列的表头文本（应对跨行表头）。"""
    ncols = max((len(r) for r in table), default=0)
    headers = {c: "" for c in range(ncols)}
    for row in table[:scan_rows]:
        for c in range(len(row)):
            if row[c]:
                headers[c] += " " + str(row[c]).replace("\n", "")
    return headers


def _find_header_col(headers: Dict[int, str], aliases: List[str]) -> Optional[int]:
    """返回第一个表头文本命中任一别名的列索引（最左 = 当期）。"""
    for c in sorted(headers.keys()):
        h = headers[c]
        if any(a in h for a in aliases):
            return c
    return None


def _locate_data_col(table: List[list], header_col: Optional[int],
                     predicate, min_hits: int = 2) -> Optional[int]:
    """
    找"值类型匹配、且离表头列最近"的列。
    header_col=None 时返回 None（表头没这个语义列 → 不取）。

    扫描所有行（表头行的目标列一般不含金额/百分比，不会误计），
    阈值取 min(min_hits, 数据规模) 以兼容小表。
    """
    if header_col is None:
        return None
    ncols = max((len(r) for r in table), default=0)
    need = min(min_hits, max(1, len(table) - 1))   # 小表降低门槛
    candidates = []
    for c in range(ncols):
        hits = sum(1 for row in table if c < len(row) and predicate(row[c]))
        if hits >= need:
            candidates.append(c)
    if not candidates:
        return None
    # 离表头列最近的候选列（同距离取更靠左）
    return min(candidates, key=lambda c: (abs(c - header_col), c))


def detect_columns_by_header(table: List[list], aliases: Dict[str, List[str]],
                             scan_rows: int = 3) -> Dict[str, Optional[int]]:
    """
    表头驱动认列。

    Args:
        table: pdfplumber 还原的二维数组
        aliases: {语义名: [表头别名...]}，来自 revenue.yaml 的 header_aliases
        scan_rows: 视作表头的前 N 行

    Returns:
        {语义名: 数据列索引或 None}，附 "_header_cols" 调试信息
    """
    headers = _column_headers(table, scan_rows)
    result: Dict[str, Optional[int]] = {}
    header_cols: Dict[str, Optional[int]] = {}
    for target, alias_list in aliases.items():
        hc = _find_header_col(headers, alias_list)
        header_cols[target] = hc
        pred = _PREDICATES.get(target, _is_text)
        result[target] = _locate_data_col(table, hc, pred)
    result["_header_cols"] = header_cols
    return result


def find_ratio_sum_column(table: List[list], lo: float = 90, hi: float = 110) -> Optional[int]:
    """
    值驱动找占比列：某列的 %-值跨行求和落在 [lo,hi]（≈100）→ 判为占比列。
    用于 pdfplumber 没捕获"占营业收入比重"表头时的兜底。
    毛利率列各行是独立毛利率、求和远不到 100，天然被排除。
    """
    ncols = max((len(r) for r in table), default=0)
    for c in range(ncols):
        vals = []
        for row in table:
            if c < len(row) and row[c] and "%" in str(row[c]):
                try:
                    v = float(str(row[c]).replace("%", "").replace(",", "").strip())
                    if 0 <= v <= 100:
                        vals.append(v)
                except ValueError:
                    pass
        if len(vals) < 2:
            continue
        # 去掉可能的合计行(100)，看其余是否求和≈100
        non_total = [v for v in vals if v < 99.5]
        if lo <= sum(non_total) <= hi or lo <= sum(vals) <= hi:
            return c
    return None


def classify_revenue_table(table: List[list], rule: dict) -> str:
    """
    判定营收表类型：composition(占比构成表) / margin(毛利率表) / unknown。
    1) 表头命中"占营业收入比重"→ composition
    2) 表头无占比但有某 %-列求和≈100 → composition（兜底，治表头未被捕获）
    3) 表头只有毛利率 → margin
    """
    headers_text = " ".join(_column_headers(table, scan_rows=4).values())
    types = (rule or {}).get("table_types", {})
    comp = types.get("composition", {}).get("require_any_header", [])
    marg = types.get("margin", {}).get("require_any_header", [])
    has_comp = any(a in headers_text for a in comp)
    has_marg = any(a in headers_text for a in marg)
    if has_comp:
        return "composition"     # 有占比列优先判为构成表（即便也有毛利率）
    if has_marg:
        return "margin"
    return "unknown"
    # 注：曾尝试"值求和≈100"值驱动兜底分类，但会把"同比增长%"列误判，已回滚。
    # 表头未被捕获的占比表识别留待 M3（更稳健的方案）。
