"""
版式指纹 — Phase 1.3

为一份财报 PDF 生成稳定的"版式指纹"，用途：
  1. 解析器选择：同一指纹的文档用同一套解析策略
  2. 经验复用：经验库按指纹归并（Phase 5.2），而非仅按 stock_code
  3. 新版式识别：指纹首次出现 → 可能需要新解析器

指纹只依赖 PyMuPDF 文本（快、确定性），不解析表格。

用法：
  from src.parsers.layout_fingerprint import compute_fingerprint
  fp = compute_fingerprint("xxx.pdf")
  fp["hash"]       # 短稳定哈希，用于归并
  fp["doc_type"]   # bank / securities / normal
"""

import hashlib
from typing import Dict, List

import fitz


# 文档大类识别特征：(关键词, 最少出现次数)。
# 用次数阈值而非"出现即判"，避免普通公司现金流量表里偶现的银行类科目误判
# （如"吸收存款"/"发放贷款"在任何公司合并现金流量表都可能各出现一次）。
_DOC_TYPE_MARKERS = {
    "bank": [("非利息净收入", 2), ("利息净收入", 3)],          # 银行特有，普通公司为 0
    "securities": [("手续费及佣金净收入", 3), ("证券经纪业务", 1)],
    "insurance": [("已赚保费", 2), ("退保金", 1)],
}

# 结构性关键词类别（出现与否构成指纹的主体）
_STRUCT_KEYWORDS = {
    "rev_by_product": ["分产品", "分行业", "分地区"],
    "rev_construct": ["营业收入构成", "主营业务收入"],
    "rnd": ["研发费用", "研发投入", "职工薪酬"],
    "employee": ["专业构成", "教育程度", "在职员工"],
    "cost": ["营业成本构成", "占营业成本比重"],
    "supplier": ["前五名供应商", "前五大供应商"],
    "client": ["前五名客户", "前五大客户"],
    "notes": ["财务报表附注", "财务报告附注"],
}


def _detect_doc_type(full_text: str) -> str:
    for dtype, markers in _DOC_TYPE_MARKERS.items():
        # 该类型的所有 (关键词,阈值) 都满足才判定
        if all(full_text.count(kw) >= n for kw, n in markers):
            return dtype
    return "normal"


def compute_fingerprint(pdf_path: str, max_pages: int = 250) -> Dict:
    """计算版式指纹。失败时返回 doc_type=unknown 的降级指纹。"""
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return {"doc_type": "unknown", "hash": "unknown", "error": str(e),
                "page_count": 0, "keyword_categories": [], "keyword_pages": {}}

    page_count = len(doc)
    scan_n = min(page_count, max_pages)

    # 累积全文（用于 doc_type）+ 记录每类关键词首次出现的相对页位置
    parts = []
    keyword_pages: Dict[str, int] = {}
    for pn in range(scan_n):
        text = doc[pn].get_text("text")
        parts.append(text)
        for cat, kws in _STRUCT_KEYWORDS.items():
            if cat in keyword_pages:
                continue
            if any(kw in text for kw in kws):
                # 用相对页位置（分桶）让指纹对页数差异更鲁棒
                keyword_pages[cat] = pn + 1
    doc.close()

    full_text = "\n".join(parts)
    doc_type = _detect_doc_type(full_text)
    categories = sorted(keyword_pages.keys())

    # 相对位置分桶（10 档），让同版式不同年份/页数的文档归到同一指纹
    def _bucket(p: int) -> int:
        return int(p / max(page_count, 1) * 10) if page_count else 0

    cat_buckets = {c: _bucket(keyword_pages[c]) for c in categories}

    sig_str = f"{doc_type}|" + "|".join(f"{c}:{cat_buckets[c]}" for c in categories)
    fp_hash = hashlib.md5(sig_str.encode("utf-8")).hexdigest()[:12]

    return {
        "doc_type": doc_type,
        "hash": fp_hash,
        "signature": sig_str,
        "page_count": page_count,
        "keyword_categories": categories,
        "keyword_pages": keyword_pages,
        "keyword_buckets": cat_buckets,
    }
