"""
PDF 定位器 — 缓存优先，未命中则按需下载（复用 book-agent 稳定下载方案）
========================================================================

收敛原先散落在 5+ 处的 `_pdf_path` 逻辑，并加"缓存未命中→下载"能力：
  ensure_pdf(code, year):
    1) 先在 PDF_CACHE_DIR 里按 {code}_{year}*.pdf 通配匹配（原逻辑）
    2) 命中 → 直接返回
    3) 未命中 → 调 book-agent/web/pdf_pipeline.ensure_annual_pdf
       （巨潮按代码查该会计年度年报直链 → 下载到同一缓存目录 → 返回路径）
    4) 仍失败 → None

下载方案不自研，直接复用 book-agent 里已跑通的巨潮客户端(cninfo_client + pdf_pipeline)，
避免重复实现与口径漂移。
"""

import sys
import glob
from pathlib import Path
from typing import Optional

from src.config import Config


def find_cached(code: str, year: int) -> Optional[str]:
    """只查缓存：{code}_{year}*.pdf 通配匹配，取排序第一个；无则 None。"""
    hits = sorted(glob.glob(str(Path(Config.PDF_CACHE_DIR) / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def _book_agent_root() -> Optional[Path]:
    """定位 book-agent 项目根(含 web/ 目录)，用于 import 其下载管线。"""
    import os
    candidates = [
        os.getenv("BOOK_AGENT_DIR"),
        Path(__file__).resolve().parents[3].parent / "book-agent",   # FinParseAI 的同级
        Path(Config.PDF_CACHE_DIR).resolve().parent.parent,          # 缓存默认在 book-agent/output/pdf_cache
    ]
    for c in candidates:
        if c and (Path(c) / "web" / "pdf_pipeline.py").exists():
            return Path(c)
    return None


def download_pdf(code: str, year: int) -> Optional[str]:
    """按需下载（复用 book-agent 稳定方案）。成功返回本地路径，失败 None。"""
    root = _book_agent_root()
    if not root:
        return None
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from web.pdf_pipeline import ensure_annual_pdf   # book-agent 的巨潮下载管线
        dest, _url, err = ensure_annual_pdf(code, year, cache_dir=Path(Config.PDF_CACHE_DIR))
        if dest and not err:
            return str(dest)
    except Exception:
        pass
    return None


def ensure_pdf(code: str, year: int, download: bool = True) -> Optional[str]:
    """
    定位 PDF：缓存优先，未命中且 download=True 时按需下载。
    入参：code(股票代码) / year(会计年度) / download(是否允许联网下载)。
    返回：本地 PDF 路径 或 None。
    """
    p = find_cached(code, year)
    if p:
        return p
    if not download:
        return None
    if download_pdf(code, year):
        return find_cached(code, year)   # 下载后再从缓存取(命名带 hash，统一走 glob)
    return None
