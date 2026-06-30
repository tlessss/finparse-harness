"""table_scanner.py 测试 — 抽表 + filter_by_signature。"""

import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".")
sys.path.insert(0, ROOT)

from src.parsers.infra.table_scanner import (
    SECTION_FUZHU,
    SECTION_MGMT,
    SECTION_OTHER,
    filter_by_signature,
    scan_pdf,
)

SAMPLE_CODE, SAMPLE_YEAR = "000878", 2025
SAMPLE_NAME = "云南铜业"


def _resolve_sample_pdf() -> str:
    from src.config import Config
    hits = sorted(Config.PDF_CACHE_DIR.glob(f"{SAMPLE_CODE}_{SAMPLE_YEAR}*.pdf"))
    if hits:
        return str(hits[0])
    fallback = os.path.join(ROOT, "pdfs", "多氟多-2025.pdf")
    return fallback if os.path.exists(fallback) else ""


SAMPLE_PDF = _resolve_sample_pdf()
_has_sample = bool(SAMPLE_PDF and os.path.exists(SAMPLE_PDF))
_skip_reason = (
    f"样本 PDF 不存在（期望 {SAMPLE_CODE}_{SAMPLE_YEAR} 于 PDF_CACHE_DIR）"
    if not _has_sample
    else ""
)


def _scan_item(table, text, section=SECTION_FUZHU, page=20):
    return {"table": table, "text": text, "section": section, "page": page}


def _revenue_like_grid(n_data_rows=6):
    """合成一张像营收构成的表（≥8 行，含占比列）。"""
    rows = [
        ["项目", "营业收入", "占营业收入比重"],
        ["分产品", None, None],
    ]
    for i in range(n_data_rows):
        rows.append([f"产品{i + 1}", f"{(i + 1) * 1000:.2f}", f"{10 + i}.0%"])
    rows.append(["合计", "21000.00", "100.0%"])
    return rows


def _text_for(grid, extra="营业收入 分产品 分行业 分地区"):
    body = " ".join(c for row in grid for c in row if c)
    return f"{extra} {body}"


# ── filter_by_signature（合成表，不依赖 PDF）──

def test_filter_by_signature_revenue_picks_best():
    good = _revenue_like_grid()
    noise = [["员工", "人数"], ["生产人员", "100"], ["销售人员", "50"]] * 4
    tables = [
        _scan_item(good, _text_for(good), page=103),
        _scan_item(noise, "员工 专业构成 在职员工 " * 3, page=50),
    ]
    hits = filter_by_signature(tables, "revenue")
    assert len(hits) >= 1
    assert hits[0]["page"] == 103
    assert hits[0]["score"] >= 20
    assert "table" in hits[0] and "score" in hits[0]


def test_filter_by_signature_revenue_requires_must_have():
    # 表内 deliberately 不含 must_have 任一关键词
    grid = [
        ["项目", "本期", "比例"],
        ["类别A", "1000.00", "50.0%"],
        ["类别B", "800.00", "40.0%"],
        ["类别C", "200.00", "10.0%"],
        ["项4", "100.00", "1.0%"],
        ["项5", "100.00", "1.0%"],
        ["项6", "100.00", "1.0%"],
        ["项7", "100.00", "1.0%"],
    ]
    text = " ".join(c for row in grid for c in row if c)
    tables = [_scan_item(grid, text, page=1)]
    assert filter_by_signature(tables, "revenue") == []


def test_filter_by_signature_revenue_exclude_keyword():
    grid = _revenue_like_grid()
    tables = [_scan_item(grid, _text_for(grid) + " 供应商 前五名", page=1)]
    hits = filter_by_signature(tables, "revenue")
    # 命中 exclude「供应商」会 -60，可能仍 >=20 或被淘汰；至少得分应低于无 exclude 版
    clean = filter_by_signature(
        [_scan_item(grid, _text_for(grid), page=1)], "revenue"
    )
    if hits:
        assert hits[0]["score"] < clean[0]["score"]


def test_filter_by_signature_skips_too_few_rows():
    short = [["营业收入", "金额", "占比"], ["A", "1", "100%"]]
    tables = [_scan_item(short, "营业收入 分产品 " + " ".join(short[0]), page=1)]
    assert filter_by_signature(tables, "revenue") == []


def test_filter_by_signature_section_bonus():
    grid = _revenue_like_grid()
    text = _text_for(grid)
    in_fuzhu = filter_by_signature(
        [_scan_item(grid, text, section=SECTION_FUZHU)], "revenue"
    )
    in_other = filter_by_signature(
        [_scan_item(grid, text, section=SECTION_OTHER)], "revenue"
    )
    assert in_fuzhu and in_other
    assert in_fuzhu[0]["score"] > in_other[0]["score"]


def test_filter_by_signature_unknown_type_empty():
    assert filter_by_signature([], "not_a_real_sig") == []


# ── 集成：000878 缓存全量表 ──

@pytest.mark.skipif(not _has_sample, reason=_skip_reason)
def test_filter_by_signature_revenue_on_000878_cache():
    from src.eval.table_cache import get_tables

    tables = get_tables(SAMPLE_CODE, SAMPLE_YEAR)
    assert tables and len(tables) >= 100
    hits = filter_by_signature(tables, "revenue")
    assert hits, f"{SAMPLE_NAME} 缓存表上应筛出营收候选"
    assert hits[0]["score"] >= 20
    # 返回按分数降序
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.skipif(not _has_sample, reason=_skip_reason)
def test_scan_pdf_returns_expected_shape():
    tables = scan_pdf(SAMPLE_PDF, max_pages=30)
    assert tables, f"{SAMPLE_NAME} 应至少抽到一张表"
    item = tables[0]
    for key in ("page", "table", "text", "section", "cell_bbox", "table_bbox"):
        assert key in item
    assert item["page"] >= 16


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
