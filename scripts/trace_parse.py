"""
解析过程追踪器 — 把营收解析的 6 个步骤逐一打印出来

用真实 PDF 演示"PDF → 散乱格子 → 认列 → 切桶逐行 → 结构化 → 硬规则"全过程，
既能看成功案例（如多氟多），也能看失败案例（占比之和漏行）做对照。

用法：
  python3 -m scripts.trace_parse --pdf pdfs/多氟多-2025.pdf
  python3 -m scripts.trace_parse --stock 000333 --year 2025      # 从缓存按代码找
  python3 -m scripts.trace_parse --stock 000425                  # 看一个失败案例
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.parsers.revenue.default import RevenueParser
from src.validators.hard_rules import check_hard_rules


def _resolve_pdf(args) -> str:
    if args.pdf:
        return args.pdf
    cache = Config.PDF_CACHE_DIR
    for f in cache.iterdir():
        if f.suffix != ".pdf":
            continue
        parts = f.stem.split("_")
        if len(parts) >= 2 and parts[0] == args.stock and parts[1] == str(args.year):
            return str(f)
    raise SystemExit(f"未找到缓存 PDF: {args.stock}_{args.year}.pdf")


def trace(pdf_path: str, max_grid_rows: int = 6):
    print(f"\n{'='*64}\n  营收解析追踪: {os.path.basename(pdf_path)}\n{'='*64}")
    p = RevenueParser({})

    # ── 步骤1: PyMuPDF 读文本，搜关键词找候选页 ──
    pages = p._find_candidate_pages(pdf_path)
    print(f"\n【步骤1】PyMuPDF(fitz) 读文本搜营收关键词 → 候选页: {pages[:10]}")
    if not pages:
        print("  ❌ 没找到候选页（关键词没命中）→ 营收字段会是空")
        return

    # ── 步骤2: pdfplumber 在候选页抽表 ──
    tables = p._extract_tables(pdf_path, pages)
    print(f"\n【步骤2】pdfplumber 在候选页 extract_tables() → "
          f"抽出 {len(tables)} 张含分产品/行业/地区的表")
    if not tables:
        print("  ❌ pdfplumber 没抽到符合条件的表 → 营收字段会是空")
        return

    # ── 步骤3: 打分挑最佳表 + 看 pdfplumber 还原的真实格子 ──
    ranked = p._filter_revenue_tables(tables)
    if not ranked:
        print("  ❌ 所有表打分都不达标 → 营收字段会是空")
        return
    best = ranked[0]
    print(f"\n【步骤3】打分挑出最佳表（共 {len(best)} 行）。"
          f"pdfplumber 还原的二维格子，前 {max_grid_rows} 行（∅=空格，单元格截断显示）：")
    for i, row in enumerate(best[:max_grid_rows]):
        cells = [(c[:8] if c else "∅") for c in row]
        print(f"   行{i}: {cells}")

    # ── 步骤4: 表头驱动认列（M1）──
    name_col, amount_col, ratio_col = p._resolve_columns(best)
    stat = p._detect_columns(best)
    print(f"\n【步骤4】表头驱动认列（占比闸门）→ "
          f"名称列={name_col}  金额列={amount_col}  占比列={ratio_col}")
    print(f"         (对比·旧统计法: 名称={stat[0]} 金额={stat[1]} 占比={stat[2]}"
          f" —— 占比列若被改成 None，即闸门拦下了'毛利率冒充占比')")

    # ── 步骤5: 切桶 + 逐行抽取 + 跳合计行 ──
    result = p.parse(pdf_path).get("revenue_breakdown") or {}
    print(f"\n【步骤5】按分产品/行业/地区切桶，逐行抽取（跳合计行/表头/去重）：")
    for dim, label in [("segments", "分产品"), ("industries", "分行业"), ("regions", "分地区")]:
        items = result.get(dim) or []
        if not items:
            continue
        s = sum(i["ratio_pct"] for i in items if i.get("ratio_pct") is not None)
        flag = "✅" if 98 <= s <= 102 else "⚠️"
        print(f"   {label} ({len(items)}项, 占比和={round(s,1)}% {flag}):")
        for it in items:
            print(f"      {(it['name'] or ''):10s} 占比={it.get('ratio_pct')}%  金额={it.get('revenue_yuan')}")

    # ── 步骤6: 硬规则红线校验 ──
    h = check_hard_rules({"revenue_breakdown": result})
    print(f"\n【步骤6】硬规则红线校验 → {'✅ 通过' if h['passed'] else '❌ 红线'} "
          f"(red={h['red_count']} warn={h['warn_count']})")
    for v in h["violations"]:
        if v["severity"] == "red":
            print(f"   ❌ {v['detail']}")
    print()


def main():
    ap = argparse.ArgumentParser(description="营收解析过程追踪")
    ap.add_argument("--pdf", type=str, default=None, help="直接指定 PDF 路径")
    ap.add_argument("--stock", type=str, default=None, help="股票代码（从缓存找）")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--rows", type=int, default=6, help="打印的原始格子行数")
    args = ap.parse_args()
    if not args.pdf and not args.stock:
        args.pdf = "pdfs/多氟多-2025.pdf"   # 默认演示样本
    trace(_resolve_pdf(args), max_grid_rows=args.rows)


if __name__ == "__main__":
    main()
