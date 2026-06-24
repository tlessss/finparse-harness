"""
通用表格扫描 + 内容特征识别工具

职责：
  1. 扫描 PDF 提取表格
  2. 章节上下文标记（附注 vs 管理层讨论 vs 其它）
  3. 表格类型特征识别
"""

from typing import List, Dict, Optional
import pdfplumber
import fitz

# ── 章节上下文标记 ──

# 章节类型
SECTION_FUZHU = "fuzhu"        # 财务报表附注
SECTION_MGMT = "management"     # 管理层讨论与分析
SECTION_OTHER = "other"         # 其它（封面、目录等）

# 章节切换关键词（按优先级排序）
_SECTION_MARKERS = [
    # 附注开始标记
    ("财务报表附注", SECTION_FUZHU),
    ("财务报告附注", SECTION_FUZHU),
    ("会计报表附注", SECTION_FUZHU),
    # 管理层讨论开始标记
    ("管理层讨论与分析", SECTION_MGMT),
    ("经营情况讨论与分析", SECTION_MGMT),
    ("董事会报告", SECTION_MGMT),
    # 附注结束标记（回到其它）
    ("公司代码", SECTION_OTHER),
    ("备查文件", SECTION_OTHER),
    ("董事、监事", SECTION_OTHER),
]


def detect_page_context(pdf_path: str) -> Dict[int, str]:
    """
    用 PyMuPDF 扫描全文，标记每页属于哪个章节区域。

    Returns:
        {page_num: section_type}
        页码从 1 开始计数
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return {}

    context = {}  # {page_num: section}
    current_section = SECTION_OTHER

    for pn in range(len(doc)):
        text = doc[pn].get_text("text")

        # 检测章节切换
        for marker, section_type in _SECTION_MARKERS:
            if marker in text:
                current_section = section_type
                break

        context[pn + 1] = current_section

    doc.close()
    return context


# ── 表格提取 ──

def _align_bbox(grid: list, cell_bbox: list) -> list:
    """把 cell_bbox 补齐成与 grid 完全同形状（缺的填 None），保证解析器按列索引取坐标不越界。"""
    out = []
    for ri, row in enumerate(grid):
        brow = cell_bbox[ri] if ri < len(cell_bbox) else []
        out.append([brow[ci] if ci < len(brow) else None for ci in range(len(row))])
    return out


def scan_pdf(pdf_path: str, max_pages: int = 200) -> List[Dict]:
    """
    扫描 PDF 正文页，提取所有表格，每张表附带页码、章节上下文与单元格坐标(溯源用)。

    用 find_tables() 替代 extract_tables()，额外保留每格 bbox（M1 溯源基建）。

    Returns:
        [{"page": int, "table": [[str]], "text": str, "section": str,
          "cell_bbox": [[(x0,y0,x1,y1)|None]],   # 与 table 同形状
          "table_bbox": (x0,y0,x1,y1)|None}, ...]
    """
    # 先扫章节上下文
    page_context = detect_page_context(pdf_path)
    results = []

    with pdfplumber.open(pdf_path) as pdf:
        end_page = min(15 + max_pages, len(pdf.pages))
        for page_num in range(15, end_page):
            page = pdf.pages[page_num]
            pn = page_num + 1
            section = page_context.get(pn, SECTION_OTHER)

            for tbl in page.find_tables():
                try:
                    grid = tbl.extract()
                except Exception:
                    continue
                if not grid:
                    continue
                # 与 grid 同形状的坐标网格
                cell_bbox = []
                for row in tbl.rows:
                    cells = getattr(row, "cells", None) or []
                    cell_bbox.append([tuple(c) if c else None for c in cells])
                cell_bbox = _align_bbox(grid, cell_bbox)

                text = " ".join(c.replace("\n", " ") for row in grid for c in row if c)
                results.append({
                    "page": pn,
                    "table": grid,
                    "text": text,
                    "section": section,
                    "cell_bbox": cell_bbox,
                    "table_bbox": tuple(tbl.bbox) if getattr(tbl, "bbox", None) else None,
                })

    return results


# ── 表格类型特征配置 ──

# 每个表格类型允许的章节上下文
SECTION_ALLOWED = {
    "revenue": [SECTION_FUZHU],
    "rnd": [SECTION_FUZHU],
    "employee": [SECTION_FUZHU],
    "cost": [SECTION_FUZHU],
    "supplier": [SECTION_FUZHU],
}

# 各表格类型的计分特征
TABLE_SIGNATURES = {
    "revenue": {
        "must_have": ["营业收入", "分产品", "分行业", "分地区"],
        "exclude": ["员工", "供应商", "研发", "不良", "覆盖率", "充足率",
                     "资本充足", "净息差", "净利差"],
        "min_rows": 8,
        "max_rows": 30,
        "ratio_max": 100,      # 占比不能超过 100
    },
    "rnd": {
        "must_have": ["职工薪酬", "研发材料", "研发费用"],
        "exclude": ["营业收入", "分产品", "专业构成", "供应商", "客户",
                     "生产人员", "销售人员", "教育程度",
                     "占比", "余额", "利息", "不良", "充足率",
                     "销售费用", "管理费用"],
        "min_rows": 5,
        "max_rows": 15,
        "ratio_max": None,
    },
    "employee": {
        "must_have": ["专业构成", "教育程度", "员工", "在职员工"],
        "exclude": [],
        "min_rows": 8,
        "max_rows": 25,
        "ratio_max": None,
    },
    "cost": {
        "must_have": ["占营业成本比重", "营业成本构成", "成本构成", "成本"],
        "exclude": ["研发", "员工"],
        "min_rows": 3,
        "max_rows": 20,
        "ratio_max": 100,
    },
    "supplier": {
        "must_have": ["供应商名称", "客户名称", "前五名", "采购额", "销售额（元）", "销售额"],
        "exclude": [],
        "min_rows": 3,
        "max_rows": 15,
        "ratio_max": None,
    },
}


def filter_by_signature(tables: List[Dict], sig_type: str,
                        enforce_section: bool = True) -> list:
    """
    根据表格类型特征 + 章节上下文筛选表格。

    Args:
        tables: scan_pdf 返回的全量表格列表
        sig_type: "revenue" / "rnd" / "employee" / "cost" / "supplier"
        enforce_section: 是否强制要求在附注章节内

    Returns:
        匹配的表格列表，按得分降序排列
    """
    sig = TABLE_SIGNATURES.get(sig_type, {})
    must_have = sig.get("must_have", [])
    exclude = sig.get("exclude", [])
    min_rows = sig.get("min_rows", 5)
    max_rows = sig.get("max_rows", 40)
    ratio_max = sig.get("ratio_max")
    allowed_sections = SECTION_ALLOWED.get(sig_type, [])

    scored = []
    for item in tables:
        t = item["table"]
        text = item["text"]
        section = item.get("section", SECTION_OTHER)
        score = 0

        score = 0

        # 上下文过滤：附注章节大加分，其它区域减分
        if enforce_section and allowed_sections:
            if section in allowed_sections:
                score += 30
            else:
                score -= 15

        # 行数过滤
        if len(t) < min_rows or len(t) > max_rows:
            continue

        # 正向特征：至少命中 1 个 must_have
        must_hit = 0
        for kw in must_have:
            if kw in text:
                score += 25
                must_hit += 1

        # 必须命中至少 1 个 must_have（如果配置了 must_have 的话）
        if must_have and must_hit == 0:
            continue

        # 排除特征
        for kw in exclude:
            if kw in text:
                score -= 60
                break  # 命中一个排除词即可大幅扣分

        # 有占比列 +20
        cols = detect_column_types(t)
        if cols["ratio_col"] is not None:
            score += 20
        if cols["amount_col"] is not None:
            score += 15

        # 有中文名称
        names = sum(1 for row in t for c in row if c and _is_text(c))
        if names >= 5:
            score += 10

        # 占比最大值校验（防止将同比%误当占比%）
        if ratio_max is not None and cols["ratio_col"] is not None:
            max_ratio_in_table = 0
            for row in t:
                if cols["ratio_col"] < len(row):
                    v = row[cols["ratio_col"]]
                    if v and "%" in v:
                        try:
                            r = abs(float(v.replace("%", "").replace(",", "").strip()))
                            max_ratio_in_table = max(max_ratio_in_table, r)
                        except ValueError:
                            pass
            if max_ratio_in_table > ratio_max:
                score -= 50  # 占比超出范围，强烈排除

        if score >= 20:
            scored.append((score, item["table"], item["page"]))

    scored.sort(key=lambda x: -x[0])
    return [{"table": t, "page": p, "score": s} for s, t, p in scored]


# ── 列类型检测 ──

def detect_column_types(table: list) -> Dict:
    """
    检测表格各列的类型。

    Returns:
        {"name_col": int, "amount_col": int, "ratio_col": int, ...}
    """
    num_cols = max(len(row) for row in table) if table else 0
    if num_cols == 0:
        return {"name_col": 0, "amount_col": None, "ratio_col": None,
                "col_count": 0, "row_count": 0}

    col_stats = {i: {"text": 0, "number": 0, "ratio": 0} for i in range(num_cols)}

    for row in table:
        for ci in range(len(row)):
            v = row[ci]
            if not v:
                continue
            cv = v.replace("\n", " ").strip()
            if not cv:
                continue
            if "%" in cv:
                col_stats[ci]["ratio"] += 1
            elif _looks_like_money(cv):
                col_stats[ci]["number"] += 1
            elif _is_text(cv):
                col_stats[ci]["text"] += 1

    # 占比列
    ratio_col = None
    for ci in range(num_cols):
        vals = []
        for row in table:
            if ci < len(row):
                v = row[ci]
                if v and "%" in v:
                    try:
                        vals.append(float(v.replace("%", "").replace(",", "").strip()))
                    except ValueError:
                        pass
        valid = [x for x in vals if 0 <= x <= 100]
        if len(valid) >= 3:
            if ratio_col is None or len(valid) > sum(1 for row in table if ci < len(row) and row[ci] and "%" in row[ci]):
                ratio_col = ci

    # 金额列
    amount_col = None
    max_money = 0
    for ci in range(num_cols):
        cnt = sum(1 for row in table if ci < len(row) and row[ci] and _looks_like_money(row[ci]))
        if cnt > max_money and cnt >= 3:
            max_money = cnt
            amount_col = ci

    if amount_col == ratio_col:
        amount_col = None
        for ci in range(num_cols):
            if ci != ratio_col:
                cnt = sum(1 for row in table if ci < len(row) and row[ci] and _looks_like_money(row[ci]))
                if cnt >= 2:
                    amount_col = ci
                    break

    # 名称列
    name_col = None
    for ci in range(num_cols):
        if ci == amount_col or ci == ratio_col:
            continue
        cnt = sum(1 for row in table if ci < len(row) and row[ci] and _is_text(row[ci]))
        if cnt >= 3:
            name_col = ci
            break
    if name_col is None:
        candidates = sorted(col_stats.keys(), key=lambda i: col_stats[i]["text"], reverse=True)
        for c in candidates:
            if c != amount_col and c != ratio_col:
                name_col = c
                break
        if name_col is None and candidates:
            name_col = candidates[0]

    return {
        "name_col": name_col or 0,
        "amount_col": amount_col,
        "ratio_col": ratio_col,
        "col_count": num_cols,
        "row_count": len(table),
    }


# ── 工具 ──

def detect_section_labels(table: list) -> List[Optional[str]]:
    labels = {"分行业": "industries", "分产品": "segments", "分地区": "regions"}
    result = []
    for row in table:
        found = None
        for c in row:
            if c and c.strip() in labels:
                found = labels[c.strip()]
                break
        result.append(found)
    return result


_TOTAL_KEYWORDS = ("合计", "总计", "小计", "总额", "合計", "总 计", "小 计", "合 计")
_GRAND_TOTAL_NAMES = ("营业收入", "主营业务收入", "营业总收入", "营业收入合计",
                      "营业收入总额", "主营业务收入合计", "营业成本")


def is_total_row(name: str) -> bool:
    """判断某行名称是否为合计/小计/总计行（应从分项明细中剔除，避免占比之和翻倍）。"""
    if not name:
        return False
    n = name.replace(" ", "").replace("　", "").strip()
    if not n:
        return False
    if any(t.replace(" ", "") in n for t in _TOTAL_KEYWORDS):
        return True
    if n in _GRAND_TOTAL_NAMES:
        return True
    return False


def cell_str(cells: list, idx: int) -> str:
    if idx >= len(cells):
        return ""
    return cells[idx].strip() if cells[idx] else ""


def parse_money(s: str):
    if not s:
        return None
    s = s.replace(",", "").replace("，", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_ratio(s: str):
    if not s:
        return None
    s = s.replace("(", "-").replace("（", "-").replace(")", "").replace("）", "")
    s = s.replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _looks_like_money(s: str) -> bool:
    s = s.replace(",", "").replace("，", "").strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_text(s: str) -> bool:
    if not s:
        return False
    return any('\u4e00' <= c <= '\u9fff' for c in s)
