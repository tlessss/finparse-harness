"""M1 溯源基建验收测试（用真实 PDF）"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.parsers.infra.table_scanner import scan_pdf
from src.parsers.revenue.default import RevenueParser

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pdfs", "多氟多-2025.pdf")


def test_scan_pdf_cell_bbox_shape():
    """scan_pdf 的 cell_bbox 与 table 行列同形状；非空格 bbox 为 4 元组。"""
    if not os.path.exists(SAMPLE):
        print("  (skip: 多氟多 样本缺失)"); return
    tables = scan_pdf(SAMPLE, max_pages=30)
    assert tables, "应抽到表"
    checked = 0
    for item in tables:
        grid = item["table"]; bbox = item["cell_bbox"]
        assert len(bbox) == len(grid), "行数应一致"
        for r in range(len(grid)):
            assert len(bbox[r]) == len(grid[r]), "列数应一致"
            for c in range(len(grid[r])):
                if grid[r][c]:
                    b = bbox[r][c]
                    if b is not None:
                        assert len(b) == 4, "bbox 应为 4 元组"
                        checked += 1
        assert "table_bbox" in item
    assert checked > 0, "应至少校验到一些非空格的 bbox"


def test_revenue_emits_provenance():
    """营收解析结果含溯源，且占比/收入能溯源回 (page, bbox)。"""
    if not os.path.exists(SAMPLE):
        print("  (skip: 多氟多 样本缺失)"); return
    r = RevenueParser({}).parse(SAMPLE)
    assert "溯源" in r, "应含溯源"
    prov = r["溯源"]
    assert prov, "溯源不应为空"
    # 至少有一条占比 / 收入溯源
    has_ratio = any(k.endswith(".ratio_pct") for k in prov)
    has_rev = any(k.endswith(".revenue_yuan") for k in prov)
    assert has_ratio and has_rev, f"应有占比和收入溯源: {list(prov)[:5]}"
    for k, v in prov.items():
        assert isinstance(v["page"], int) and v["page"] > 0
        assert len(v["bbox"]) == 4


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print(f"  ✅ {fn.__name__}"); p += 1
        except Exception:
            print(f"  ❌ {fn.__name__}"); traceback.print_exc(); f += 1
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
