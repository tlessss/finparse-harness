"""
章节/页面定位（按相关性打分）— 找页改造 第①步

替代旧的"模糊关键词 OR 匹配 + sorted()[:15] 按页码盲截断"。
核心：**按信号强度给页打分，按分数取 top-N**（不再按页码先后截断），
并对目标章节（如 MD&A）加成。这样即便真表在靠后的页，也不会被前面的
噪声页挤出候选。

用法：
  from src.parsers.infra.section_locator import rank_pages
  pages = rank_pages(pdf_path,
                     strong=["占营业收入比重", "分产品", ...],
                     weak=["营业收入", ...],
                     prefer_section="management",
                     min_page=6, top_n=12, window=1)
"""

from typing import List, Optional

import fitz

from src.parsers.infra.table_scanner import detect_page_context

# 打分权重
_W_STRONG = 10      # 每个强信号词
_W_WEAK = 1         # 每个弱信号词
_W_SECTION = 5      # 命中目标章节


def rank_pages(pdf_path: str,
               strong: List[str],
               weak: Optional[List[str]] = None,
               prefer_section: Optional[str] = None,
               min_page: int = 1,
               top_n: int = 12,
               window: int = 1) -> List[int]:
    """
    给每页按信号打分，返回分数最高的若干页（含 ±window 续表页），1-indexed。

    ── 入参格式 ──
      pdf_path       : str           PDF 路径
      strong         : list[str]     强信号词，如 ["占营业收入比重", "分产品"]，命中 +10/个
      weak           : list[str]|None 弱信号词，如 ["营业收入"]，命中 +1/个（噪声词，权重低）
      prefer_section : str|None      偏好章节标签（如 "management" MD&A），命中该章节再 +5
      min_page       : int           跳过这页之前的页(封面/目录)，默认 1
      top_n          : int           最终取分数最高的前 N 页
      window         : int           每个命中页额外带上 ±window 页(续表常跨页)
    ── 返回 ── list[int]  候选页码(从1)，升序，如 [23, 24, 25]

    打分：strong 命中数×10 + weak 命中数×1 + （在 prefer_section 章节 +5）。
    无任何 strong/weak 命中的页得 0 分，不入选。
    """
    weak = weak or []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []

    ctx = detect_page_context(pdf_path) if prefer_section else {}
    n = len(doc)
    scores = {}
    for pn in range(n):
        page_no = pn + 1
        if page_no < min_page:
            continue
        text = doc[pn].get_text("text")
        strong_hits = sum(1 for kw in strong if kw in text)
        weak_hits = sum(1 for kw in weak if kw in text)
        if strong_hits == 0 and weak_hits == 0:
            continue
        s = strong_hits * _W_STRONG + weak_hits * _W_WEAK
        if prefer_section and ctx.get(page_no) == prefer_section:
            s += _W_SECTION
        scores[page_no] = s
    doc.close()

    if not scores:
        return []

    # 按分数降序、页码升序排序，取前 top_n —— 关键：按相关性而非页码截断
    ranked = sorted(scores, key=lambda p: (-scores[p], p))[:top_n]

    # 展开 ±window 覆盖续表
    out = set()
    for p in ranked:
        for off in range(-window, window + 1):
            q = p + off
            if 1 <= q <= n:
                out.add(q)
    return sorted(out)
