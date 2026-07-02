"""
抽表缓存 — 让"多版本"迭代快起来

scan_pdf 抽表 ~100 秒/份（最慢的一步）。把每份 golden 报告的抽表结果缓存到磁盘，
之后任何解析器版本都在缓存表上跑（毫秒级），不必每个版本都重抽。
这是"多解析器 × 多版本"能高频迭代的物理前提（也是代码沙箱的输入源）。

缓存内容 = scan_pdf 原样输出（含 cell_bbox/table_bbox，JSON 里元组变列表，解析器照读不误）。

用法：
  from src.eval.table_cache import get_tables
  tables = get_tables("000425", 2025)   # 首次抽表并缓存，之后秒回
"""

import json
import os

from src.config import Config
from src.parsers.infra.table_scanner import scan_pdf

_CACHE_DIR = "goldset/tables_cache"


def _pdf_path(code: str, year: int):
    # 缓存优先，未命中则按需下载（复用 book-agent 巨潮方案）
    from src.parsers.infra.pdf_locator import ensure_pdf
    return ensure_pdf(code, year)


def put(code: str, year: int, tables) -> None:
    """把已抽好的表写进缓存（引擎已 scan_pdf，喂进来让 route 不重扫）。"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    json.dump(tables, open(os.path.join(_CACHE_DIR, f"{code}_{year}.json"), "w",
                           encoding="utf-8"), ensure_ascii=False)


def get_tables(code: str, year: int, refresh: bool = False):
    """返回该报告的抽表结果（scan_pdf 形状）；缓存命中秒回，否则抽一次存盘。"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = os.path.join(_CACHE_DIR, f"{code}_{year}.json")
    if os.path.exists(path) and not refresh:
        return json.load(open(path, encoding="utf-8"))
    pdf = _pdf_path(code, year)
    if not pdf:
        return None
    tables = scan_pdf(pdf)
    json.dump(tables, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return tables
