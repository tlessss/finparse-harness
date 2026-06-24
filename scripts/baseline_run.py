"""
基线跑批 — Phase 0.3 立标尺

对 PDF 缓存中的 2025 年报跑一遍 FinParseAI（不写库、纯离线），
输出客观基线：每字段覆盖率 + 关键字段硬规则清洁率 + 净通过率。

这是后续所有改动的对比基准：没有这个数字，无法证明 Phase 1+ 是否真的变好。

净通过（net_pass）定义（无 LLM 裁判时的保守代理指标）：
  6 个字段全部解析出 且 关键字段硬规则无 red 违规。
真·正确率需配合金标准集 / 校验器评估（见 scripts/eval_validator.py）。

用法：
  python3 -m scripts.baseline_run --limit 20            # 抽样 20 份验证
  python3 -m scripts.baseline_run --limit 0 --year 2025 # 全量 2025
  python3 -m scripts.baseline_run --limit 50 --out test_results/baseline_50.json
"""

import os
import sys
import time
import json
import argparse
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.engine_orchestrator import FinParseAI
from src.validators.hard_rules import check_hard_rules

ALL_FIELDS = ["revenue_breakdown", "rnd_info", "employees",
              "cost_breakdown", "top_clients", "top_suppliers"]


def discover_pdfs(year: int = 2025, limit: int = 0) -> list:
    """扫描缓存，按 stock_code 去重，返回 [{stock_code, year, pdf_path}]。"""
    cache_dir = Config.PDF_CACHE_DIR
    if not cache_dir.exists():
        print(f"❌ PDF 缓存目录不存在: {cache_dir}")
        return []

    seen = set()
    out = []
    for f in sorted(cache_dir.iterdir()):
        if f.suffix != ".pdf":
            continue
        parts = f.stem.split("_")
        if len(parts) < 2 or not (parts[1].isdigit() and len(parts[1]) == 4):
            continue
        code, yr = parts[0], int(parts[1])
        if year and yr != year:
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"stock_code": code, "year": yr, "pdf_path": str(f)})
    if limit > 0:
        out = out[:limit]
    return out


def run(year: int = 2025, limit: int = 20, out_path: str = None):
    pdfs = discover_pdfs(year=year, limit=limit)
    print(f"🔍 缓存目录: {Config.PDF_CACHE_DIR}")
    print(f"📋 待跑基线: {len(pdfs)} 份 ({year})\n")
    if not pdfs:
        return

    engine = FinParseAI()
    records = []
    t0 = time.time()

    # 增量写盘：跑一份存一份，崩溃也不丢已完成结果
    if out_path is None:
        out_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "test_results", f"baseline_{time.strftime('%Y%m%d_%H%M%S')}.json",
        )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    for i, p in enumerate(pdfs, 1):
        code, yr, path = p["stock_code"], p["year"], p["pdf_path"]
        rec = {"stock_code": code, "year": yr}
        start = time.time()
        try:
            r = engine.run(path, stock_code=code, report_year=yr, db_write=False)
            present = {f: bool(r.get(f)) for f in ALL_FIELDS}
            hard = check_hard_rules(r)
            rec.update({
                "field_count": r.get("field_count", 0),
                "present": present,
                "hard_passed": hard["passed"],
                "red_count": hard["red_count"],
                "warn_count": hard["warn_count"],
                "red_fields": hard["red_fields"],
                "violations": hard["violations"],
                "net_pass": (r.get("field_count", 0) == 6) and hard["passed"],
                "duration_sec": round(time.time() - start, 1),
            })
            flag = "✅" if rec["net_pass"] else ("⚠️" if hard["passed"] else "❌")
            print(f"  [{i}/{len(pdfs)}] {code} {flag} {rec['field_count']}/6 "
                  f"red={hard['red_count']} warn={hard['warn_count']} ({rec['duration_sec']}s)")
        except Exception as e:
            rec.update({"error": str(e), "traceback": traceback.format_exc()[-500:],
                        "field_count": 0, "net_pass": False})
            print(f"  [{i}/{len(pdfs)}] {code} 💥 ERROR: {e}")

        records.append(rec)
        _dump(out_path, records, year, time.time() - t0, done=False)

    summary = _dump(out_path, records, year, time.time() - t0, done=True)
    _print_summary(summary, out_path)


def _aggregate(records: list, year: int, elapsed: float) -> dict:
    n = len(records)
    ok = [r for r in records if not r.get("error")]
    field_cov = {f: sum(1 for r in ok if r.get("present", {}).get(f)) for f in ALL_FIELDS}
    return {
        "year": year,
        "total": n,
        "errored": sum(1 for r in records if r.get("error")),
        "avg_field_count": round(sum(r.get("field_count", 0) for r in records) / max(n, 1), 2),
        "field_coverage": {f: {"count": c, "pct": round(c / max(n, 1) * 100, 1)}
                           for f, c in field_cov.items()},
        "hard_clean": sum(1 for r in ok if r.get("hard_passed")),
        "hard_red": sum(1 for r in ok if not r.get("hard_passed", True)),
        "net_pass": sum(1 for r in records if r.get("net_pass")),
        "net_pass_pct": round(sum(1 for r in records if r.get("net_pass")) / max(n, 1) * 100, 1),
        "elapsed_sec": round(elapsed, 1),
    }


def _dump(path: str, records: list, year: int, elapsed: float, done: bool) -> dict:
    summary = _aggregate(records, year, elapsed)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"complete": done, "summary": summary, "records": records},
                  f, ensure_ascii=False, indent=2)
    return summary


def _print_summary(s: dict, out_path: str):
    print(f"\n{'='*64}")
    print(f"📊 基线汇总 ({s['year']})  —  共 {s['total']} 份，耗时 {s['elapsed_sec']}s")
    print(f"{'='*64}")
    print(f"  平均字段数 : {s['avg_field_count']}/6")
    print(f"  解析报错   : {s['errored']}")
    print(f"  硬规则清洁 : {s['hard_clean']}  | 触发 red: {s['hard_red']}")
    print(f"  ★ 净通过率 : {s['net_pass']}/{s['total']} = {s['net_pass_pct']}%")
    print(f"  ── 各字段覆盖率 ──")
    for f, v in s["field_coverage"].items():
        print(f"     {f:20s} {v['count']:>4}/{s['total']}  {v['pct']}%")
    print(f"\n  结果文件: {out_path}")


def main():
    ap = argparse.ArgumentParser(description="FinParseAI 基线跑批")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=20, help="0 = 全量")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()
    run(year=args.year, limit=args.limit, out_path=args.out)


if __name__ == "__main__":
    main()
