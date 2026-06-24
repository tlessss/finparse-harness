"""页面定位工具 — 跨版式自适应搜索

职责：
  1. 在 PDF 全文中用关键词找到目标表格所在的页码
  2. 支持多个关键词组合（AND/OR）
  3. 缓存结果避免重复扫描（同一份 PDF 同一 section 只搜一次）

用法：
  from src.parsers.infra.page_locator import locate_pages
  
  # 自动搜索包含"营业收入构成"或"主营业务分"的页码
  pages = locate_pages(pdf_path, ["营业收入构成", "主营业务分"])
  # pages → [24, 25, 26] 或不含关键词时 fallback 到默认
"""

import hashlib
from typing import List, Optional
import fitz

# 缓存 {(pdf_path, section_key): [page_numbers]}
_cache = {}


def locate_pages(
    pdf_path: str,
    keywords: List[str],
    fallback_pages: str = "1-50",
    context_window: int = 3,
) -> List[int]:
    """
    在 PDF 全文中搜索关键词，返回匹配的页码范围。

    Args:
        pdf_path: PDF 文件路径
        keywords: 搜索关键词列表（满足任一即可）
        fallback_pages: 搜不到时的默认页码范围（如 "1-50"）
        context_window: 关键词出现后额外扫描的页数（覆盖续表）

    Returns:
        页码列表（1-indexed）
    """
    # 缓存键
    key = hashlib.md5(f"{pdf_path}:{keywords}".encode()).hexdigest()
    if key in _cache:
        return _cache[key]

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return _parse_page_range(fallback_pages)

    matched_pages = set()

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        for kw in keywords:
            if kw in text:
                matched_pages.add(page_num + 1)
                # 拓展 context_window 页（表格可能跨页）
                for offset in range(1, context_window + 1):
                    if page_num + 1 + offset <= len(doc):
                        matched_pages.add(page_num + 1 + offset)
                    if page_num + 1 - offset >= 1:
                        matched_pages.add(page_num + 1 - offset)
                break  # 一个关键词匹配即可

    doc.close()

    if matched_pages:
        result = sorted(matched_pages)
        _cache[key] = result
        return result

    # 回退到默认
    result = _parse_page_range(fallback_pages)
    _cache[key] = result
    return result


def locate_single_page(
    pdf_path: str,
    keywords: List[str],
    fallback_page: int = 50,
) -> int:
    """
    搜索单页（取第一个匹配出现的页码）。
    用于员工数据等只出现在 1-2 页的表格。
    """
    pages = locate_pages(pdf_path, keywords, str(max(fallback_page - 5, 1)))
    return pages[0] if pages else fallback_page


def _parse_page_range(range_str: str) -> List[int]:
    """解析 "24-26" 或 "205" 为页码列表"""
    nums = []
    for part in range_str.split(","):
        part = part.strip()
        if "-" in part:
            s, e = part.split("-", 1)
            nums.extend(range(int(s.strip()), int(e.strip()) + 1))
        else:
            nums.append(int(part))
    return nums


def clear_cache():
    """清除页码缓存（测试用或 PDF 改变时调用）"""
    _cache.clear()
