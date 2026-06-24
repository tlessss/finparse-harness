"""
溯源可视化 — 测试 M1 改动效果

把营收解析结果的每个数字，按其溯源 (page,bbox) 在 PDF 原图上画红框，
导出 PNG。人眼一看红框是否正好框住那个数字，就知道溯源对不对。
这就是 M3 审核 UI"点结果→高亮原文"的底层验证。

用法：
  python3 -m scripts.show_provenance --pdf pdfs/多氟多-2025.pdf
  python3 -m scripts.show_provenance --stock 000333 --year 2025
  # 只打印不渲染：加 --no-render
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

import fitz
from src.config import Config
from src.parsers.revenue.default import RevenueParser


def _resolve(args):
    if args.pdf:
        return args.pdf
    for f in Config.PDF_CACHE_DIR.iterdir():
        if f.suffix == ".pdf":
            parts = f.stem.split("_")
            if len(parts) >= 2 and parts[0] == args.stock and parts[1] == str(args.year):
                return str(f)
    raise SystemExit(f"未找到 {args.stock}_{args.year}.pdf")


def run(pdf_path, render=True):
    r = RevenueParser({}).parse(pdf_path)
    rb = r.get("revenue_breakdown") or {}
    prov = r.get("溯源") or {}

    # 打印：值 ↔ 溯源
    print(f"\n{'='*70}\n营收解析 + 溯源: {os.path.basename(pdf_path)}\n{'='*70}")
    for dim in ["segments", "industries", "regions", "by_channel"]:
        items = rb.get(dim) or []
        if not items:
            continue
        print(f"\n[{dim}]")
        for i, it in enumerate(items):
            rp = prov.get(f"{dim}[{i}].ratio_pct")
            ap = prov.get(f"{dim}[{i}].revenue_yuan")
            loc = []
            if ap: loc.append(f"收入@p{ap['page']}")
            if rp: loc.append(f"占比@p{rp['page']}")
            print(f"  {it['name'][:16]:16s} 占比={it.get('ratio_pct')}%  "
                  f"收入={it.get('revenue_yuan')}  溯源[{', '.join(loc) or '无'}]")
    print(f"\n溯源条目: {len(prov)}")

    if not render:
        return

    # 渲染：按页分组画框
    by_page = {}
    for path, v in prov.items():
        by_page.setdefault(v["page"], []).append((path, v["bbox"]))

    os.makedirs("test_results", exist_ok=True)
    doc = fitz.open(pdf_path)
    code = os.path.basename(pdf_path).split("_")[0].replace(".pdf", "")
    outs = []
    for page_no, boxes in sorted(by_page.items()):
        page = doc[page_no - 1]
        for _, bbox in boxes:
            rect = fitz.Rect(*bbox)
            page.draw_rect(rect, color=(1, 0, 0), width=1.2)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))   # 2x 清晰
        out = os.path.abspath(f"test_results/prov_{code}_p{page_no}.png")
        pix.save(out)
        outs.append(out)
    doc.close()
    print(f"\n✅ 已渲染高亮图 {len(outs)} 张：")
    for o in outs:
        print(f"   {o}")
    print("   打开看红框是否正好框住对应的收入/占比数字。")


def main():
    ap = argparse.ArgumentParser(description="溯源可视化")
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--stock", default=None)
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()
    if not args.pdf and not args.stock:
        args.pdf = "pdfs/多氟多-2025.pdf"
    run(_resolve(args), render=not args.no_render)


if __name__ == "__main__":
    main()
