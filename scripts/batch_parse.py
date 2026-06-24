"""
批量解析脚本 — 扫描 PDF 缓存目录，对未解析的记录执行 FinParseAI

用法:
  python3 -m scripts.batch_parse                   # 默认跑 10 份
  python3 -m scripts.batch_parse --limit 50        # 跑 50 份
  python3 -m scripts.batch_parse --stock 002407    # 跑指定股票
  python3 -m scripts.batch_parse --year 2025       # 只跑 2025 年
"""

import sys
import os
import time
import argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from pathlib import Path
from src.config import Config
from src.database import get_conn
from src.engine_orchestrator import FinParseAI


def find_pending_pdfs(limit: int = 0, stock_code: str = None, year: int = None) -> list[dict]:
    """从 PDF 缓存中找到 financial_reports 记录且未解析完成的 pdf"""
    cache_dir = Config.PDF_CACHE_DIR
    if not cache_dir.exists():
        print(f"❌ PDF 缓存目录不存在: {cache_dir}")
        return []

    # 构建缓存索引 { (code, year): path }
    pdf_index = {}
    for f in cache_dir.iterdir():
        if f.suffix != ".pdf":
            continue
        # 文件名: 000001_2025.pdf 或 000001_2025_hash.pdf
        stem = f.stem
        # 去掉末尾的 hash（如果有）
        parts = stem.split("_")
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
            code = parts[0]
            yr = int(parts[1])
            # 有 hash 也映射到同一个 code+year
            if (code, yr) not in pdf_index:
                pdf_index[(code, yr)] = str(f)

    if not pdf_index:
        print("❌ 缓存目录为空")
        return []

    # 查询 financial_reports 哪些记录还没完整解析
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            where = ["fr.report_quarter='annual'"]
            params = []
            if stock_code:
                where.append("fr.stock_code = %s")
                params.append(stock_code)
            if year:
                where.append("fr.report_year = %s")
                params.append(year)

            # 找 revenue_breakdown 或 rnd_info 有空缺的记录
            where.append("(fr.revenue_breakdown IS NULL OR fr.rnd_info IS NULL OR fr.employees IS NULL)")
            where.append("fr.data_source IN ('akshare','hybrid')")

            cur.execute(
                f"SELECT fr.id, fr.stock_code, fr.company_name, fr.report_year, "
                f"fr.revenue_breakdown IS NOT NULL AS has_rev, "
                f"fr.rnd_info IS NOT NULL AS has_rnd, "
                f"fr.employees IS NOT NULL AS has_emp "
                f"FROM financial_reports fr WHERE {' AND '.join(where)} "
                f"ORDER BY fr.stock_code, fr.report_year DESC",
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    # 只取有缓存文件的记录
    pending = []
    for row in rows:
        code = row["stock_code"].strip()
        yr = row["report_year"]
        pdf_path = pdf_index.get((code, yr))
        if pdf_path:
            pending.append({
                "report_id": row["id"],
                "stock_code": code,
                "company_name": row["company_name"],
                "report_year": yr,
                "pdf_path": pdf_path,
                "missing_fields": [
                    f for f, flag in [
                        ("revenue_breakdown", row["has_rev"]),
                        ("rnd_info", row["has_rnd"]),
                        ("employees", row["has_emp"]),
                    ] if not flag
                ],
            })

    if limit > 0:
        pending = pending[:limit]

    return pending


def run_batch(limit: int = 10, stock_code: str = None, year: int = None, dry_run: bool = False):
    """执行批量解析"""
    print(f"🔍 扫描 PDF 缓存目录: {Config.PDF_CACHE_DIR}")
    pending = find_pending_pdfs(limit=limit, stock_code=stock_code, year=year)
    print(f"📋 待解析记录: {len(pending)}")

    if not pending:
        print("🎉 没有待解析的记录")
        return

    if dry_run:
        print("\n--- DRY RUN ---")
        for p in pending:
            print(f"  {p['stock_code']} {p['company_name']:12s} {p['report_year']} "
                  f"→ 缺 {''.join(f[0].upper() for f in p['missing_fields'])}")
        return

    engine = FinParseAI()
    ok = fail = 0

    for p in pending:
        print(f"\n{'='*60}")
        print(f"📄 [{p['stock_code']}] {p['company_name']} ({p['report_year']})")
        print(f"   PDF: {p['pdf_path']}")
        print(f"   缺: {', '.join(p['missing_fields'])}")

        try:
            result = engine.run(
                p["pdf_path"],
                stock_code=p["stock_code"],
                report_year=p["report_year"],
                company_name=p["company_name"],
                db_write=True,
            )
            fields = result.get("field_count", 0)
            duration = result.get("parse_duration_sec", 0)
            db_status = result.get("db_write", "?")
            print(f"   ✅ {fields}/6 字段 | {duration}s | DB: {db_status}")
            ok += 1
        except Exception as e:
            print(f"   ❌ 解析失败: {e}")
            fail += 1

        # 每份停顿一下，避免太猛
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"📊 批量完成: ✅ {ok} 成功, ❌ {fail} 失败")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="FinParseAI 批量解析")
    parser.add_argument("--limit", type=int, default=10, help="最多解析份数（默认10）")
    parser.add_argument("--stock", type=str, default=None, help="指定股票代码")
    parser.add_argument("--year", type=int, default=None, help="指定年份")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    args = parser.parse_args()

    run_batch(limit=args.limit, stock_code=args.stock, year=args.year, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
