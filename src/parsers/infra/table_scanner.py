"""
通用表格扫描 + 内容特征识别工具（解析流程的"地基"）
========================================================

被谁用：引擎一开始调 scan_pdf() 抽全表(贵，只做一次)；signature 派解析器
(研发/员工/成本/供应商) 调 filter_by_signature() 从全表里挑出自己要的那张。

三大职责：
  1. scan_pdf            扫 PDF 抽所有表格(用 find_tables 保留每格坐标 bbox，供溯源)
  2. detect_page_context 给每页打"章节标签"(附注 / 管理层讨论MD&A / 其它)
  3. filter_by_signature 按"表格特征签名"(关键词/行数/占比上限)+ 章节，挑出某类表
  + detect_column_types  统计法认列(名称/金额/占比列)
  + is_total_row         判断"合计/小计"行(抽数时要剔除)
"""

from typing import List, Dict, Optional
import pdfplumber
import fitz

# ── 章节上下文标记 ──

# 章节类型
SECTION_FUZHU = "fuzhu"        # 财务报表附注
SECTION_MGMT = "management"     # 管理层讨论与分析
SECTION_OTHER = "other"         # 其它（封面、目录等）

# 扫描安全上限：章节驱动扫描时防极端长 PDF 跑飞(正常年报≤300页,不会触及)
_SCAN_HARD_CAP = 350

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

# PDF 大纲(书签)章节标记 —— 权威结构，优先于全文子串扫描
_TOC_MARKERS = [
    ("管理层讨论与分析", SECTION_MGMT),
    ("经营情况讨论与分析", SECTION_MGMT),
    ("董事会报告", SECTION_MGMT),
    ("财务报告", SECTION_FUZHU),
    ("财务报表", SECTION_FUZHU),
    ("备查文件", SECTION_OTHER),
]


def _context_from_toc(toc: list, npages: int) -> Dict[int, str]:
    """从 PDF 大纲(第X节)构建 页→章节 映射。无可用边界则返回 {} (让上层回退子串扫描)。"""
    bounds = []
    for level, title, page in toc:
        if level != 1:                       # 只用一级"第X节"做边界，避免子条目误切
            continue
        for marker, sec in _TOC_MARKERS:
            if marker in title:
                bounds.append((page, sec))
                break
    if not bounds:
        return {}
    bounds.sort(key=lambda x: x[0])
    ctx, cur, bi = {}, SECTION_OTHER, 0
    for pn in range(1, npages + 1):
        while bi < len(bounds) and bounds[bi][0] <= pn:
            cur = bounds[bi][1]
            bi += 1
        ctx[pn] = cur
    return ctx


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

    # ① 优先用 PDF 大纲(书签)：权威的"第X节→页码"结构，远比全文子串扫描准
    try:
        toc = doc.get_toc()
    except Exception:
        toc = None
    if toc:
        ctx = _context_from_toc(toc, len(doc))
        if ctx:
            doc.close()
            return ctx

    # ② 回退：全文子串粘性扫描(无书签的报告，如部分老年报)
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


def _caption_above(page, table_bbox, band: float = 80) -> str:
    """取表格正上方同页文本(最靠近表格的几行) = 表格标题/上文。
    比表体关键词稳得多：'（1）营业收入构成' 这类标题几乎确定性地标明表是什么。"""
    if not table_bbox:
        return ""
    try:
        top = table_bbox[1]
        if top <= 1:
            return ""
        crop = page.crop((0, max(0, top - band), page.width, top))
        txt = crop.extract_text() or ""
    except Exception:
        return ""
    lines = [ln.strip() for ln in txt.split("\n") if ln.strip()]
    return " ".join(lines[-3:])             # 紧贴表格上方的最后几行


def scan_pdf(pdf_path: str, max_pages: int = 200) -> List[Dict]:
    """
    扫描 PDF 正文页，提取所有表格，每张表附带页码、章节上下文与单元格坐标(溯源用)。

    ── 入参 ── pdf_path: str；max_pages: int 最多扫多少页(从第16页起)。
    ── 返回 ── list[dict]，每个元素一张表：
        {"page": int,                          # 页码(从1)
         "table": [[str|None, ...], ...],       # 二维字符串网格
         "text": str,                           # 整表拼成的文本(关键词匹配用)
         "section": str,                        # 章节标签 fuzhu/management/other
         "cell_bbox": [[(x0,y0,x1,y1)|None]],   # 与 table 同形状的坐标(溯源用)
         "table_bbox": (x0,y0,x1,y1)|None}

    用 find_tables() 替代 extract_tables()，就是为了多拿到 bbox 坐标(M1 溯源基建)。
    """
    # 先扫一遍全文，得到"每页属于哪个章节"
    page_context = detect_page_context(pdf_path)
    results = []

    # pdfplumber.open() → PDF 对象（上下文管理器，退出时自动关文件）
    #   pdf.pages          : list[Page]，0-indexed，pdf.pages[i] = 第 i+1 页
    #   page.width/height  : 页宽高(pt)；page.find_tables() → list[Table]
    #   tbl.extract()      : list[list[str|None]] 二维格子文本
    #   tbl.bbox           : (x0,y0,x1,y1) 整表外框；tbl.rows[].cells → 每格 bbox（原点左下角）
    with pdfplumber.open(pdf_path) as pdf:
        npages = len(pdf.pages)
        # 选扫描范围：关键是**不能用不可靠的章节去裁剪**(无书签时子串扫描会在附注中途误翻 other → 漏附注尾)。
        #   有 PDF 书签(章节可靠) → 按章节扫 management+fuzhu，跳过封面/目录/备查(other)，高效且不漏。
        #   无书签(章节不可信)   → 全扫 第16页~全文末(只用安全上限防极端长 PDF)，宁可多扫也不漏。
        try:
            _doc = fitz.open(pdf_path)
            _toc = _doc.get_toc()
            _doc.close()
            reliable = bool(_toc) and bool(_context_from_toc(_toc, npages))
        except Exception:
            reliable = False
        if reliable:
            pages_to_scan = [pn for pn in range(1, npages + 1)
                             if page_context.get(pn) in (SECTION_MGMT, SECTION_FUZHU)
                             and pn <= _SCAN_HARD_CAP]
        else:
            pages_to_scan = list(range(16, min(npages, _SCAN_HARD_CAP) + 1))

        for pn in pages_to_scan:
            page = pdf.pages[pn - 1]
            section = page_context.get(pn, SECTION_OTHER)

            for tbl in page.find_tables():            # 这一页上的每张表
                try:
                    grid = tbl.extract()              # 二维字符串网格
                except Exception:
                    continue
                if not grid:
                    continue
                # 构造与 grid 同形状的坐标网格(每格一个 bbox 或 None)
                cell_bbox = []
                for row in tbl.rows:
                    cells = getattr(row, "cells", None) or []
                    cell_bbox.append([tuple(c) if c else None for c in cells])
                cell_bbox = _align_bbox(grid, cell_bbox)   # 补齐成和 grid 完全同形状

                text = " ".join(c.replace("\n", " ") for row in grid for c in row if c)
                tbb = tuple(tbl.bbox) if getattr(tbl, "bbox", None) else None
                results.append({
                    "page": pn,
                    "table": grid,
                    "text": text,
                    "caption": _caption_above(page, tbb),   # 表格上文标题(选表主信号)
                    "section": section,
                    "cell_bbox": cell_bbox,
                    "table_bbox": tbb,
                })

    return results


# ── 表格类型特征配置 ──

# 每个表格类型允许的章节上下文
# 各字段目标表"通常所在章节"。营收/成本构成、前五大客户供应商本就在 MD&A(管理层讨论)，
# 故允许 management；附注里也常有明细 → 两者都给加分。章节是弱先验(见下方打分降权)。
SECTION_ALLOWED = {
    "revenue": [SECTION_FUZHU, SECTION_MGMT],
    "rnd": [SECTION_FUZHU, SECTION_MGMT],
    "employee": [SECTION_FUZHU, SECTION_MGMT],
    "cost": [SECTION_FUZHU, SECTION_MGMT],
    "supplier": [SECTION_FUZHU, SECTION_MGMT],
}

# 表格"上文标题"标记词 —— 选表的最强信号(标题确定性地标明表是什么，比表体关键词稳)。
# caption 强命中 → 大加分 + 豁免 must_have 硬门(救抽取乱掉、表体没词但标题清楚的表)。
_CAPTION_MARKERS = {
    "revenue": ["营业收入构成", "收入构成", "营业收入和营业成本", "主营业务分", "分部报告",
                "分行业", "分产品", "分地区", "分销售"],
    "cost": ["营业成本构成", "成本构成", "营业收入和营业成本", "主营业务分", "分部报告"],
    "rnd": ["研发投入", "研发费用"],
    "employee": ["员工情况", "员工人数", "专业构成", "教育程度", "人员构成"],
    "supplier": ["前五名", "前五大", "主要客户", "主要供应商"],
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
    从全量表里挑出某一类表(signature 派解析器用)：按"特征签名"+章节打分，选高分的。

    ── 入参 ──
      tables          : list[dict]  scan_pdf 的输出(每个含 table/text/section/page...)
      sig_type        : str  要找哪类表 "revenue"/"rnd"/"employee"/"cost"/"supplier"
                        (对应 TABLE_SIGNATURES 里的一套关键词/行数/占比上限规则)
      enforce_section : bool 是否要求该表在"附注"章节内(在=加分，不在=减分)
    ── 返回 ── list[dict]  匹配的表，按得分降序：[{"table": 二维网格, "page": int, "score": int}, ...]

    打分要点：在对的章节 +30；命中 must_have 词 +25/个(且必须至少命中1个)；
    命中 exclude 词 -60(像别类表)；有占比/金额列加分；占比列出现 >ratio_max 的值 -50(那列是同比不是占比)。
    """
    sig = TABLE_SIGNATURES.get(sig_type, {})
    must_have = sig.get("must_have", [])
    exclude = sig.get("exclude", [])
    min_rows = sig.get("min_rows", 5)
    max_rows = sig.get("max_rows", 40)
    ratio_max = sig.get("ratio_max")
    allowed_sections = SECTION_ALLOWED.get(sig_type, [])

    caption_markers = _CAPTION_MARKERS.get(sig_type, [])
    scored = []
    for item in tables:
        t = item["table"]
        text = item["text"]
        caption = item.get("caption", "")
        section = item.get("section", SECTION_OTHER)
        score = 0

        # ① caption(表格上文标题)是最强信号：'（1）营业收入构成' 这类标题确定性地标明表是什么
        cap_hit = any(m in caption for m in caption_markers)
        if cap_hit:
            score += 40

        # ② 章节：弱先验(非硬门) —— 预期章节小加分、其它小减分；主信号交给标题/表内容
        if enforce_section and allowed_sections:
            if section in allowed_sections:
                score += 20
            else:
                score -= 8

        # 行数过滤
        if len(t) < min_rows or len(t) > max_rows:
            continue

        # ③ 表体特征：至少命中 1 个 must_have
        must_hit = 0
        for kw in must_have:
            if kw in text:
                score += 25
                must_hit += 1

        # must_have 硬门 —— caption 强命中可豁免(救抽取乱掉、表体没词但标题清楚的表，如 000878)
        if must_have and must_hit == 0 and not cap_hit:
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
    统计法认列：扫一遍各列、数"有多少格像文字/金额/百分比"，据此猜各列角色。

    ── 入参 ── table: list[list[str|None]]  一张表的二维网格
    ── 返回 ── {"name_col": int, "amount_col": int|None, "ratio_col": int|None,
                "col_count": int, "row_count": int}
       占比列=有≥3个 0~100 的百分数的列；金额列=像金额最多的列；名称列=剩下中文最多的列。
       (这是兜底法；营收解析器优先用更准的"表头驱动认列"。)
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
