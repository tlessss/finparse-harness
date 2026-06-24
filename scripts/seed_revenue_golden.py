"""
营收值级 golden 的 seed 器 — 给打分器喂"真值"

作用：把人工标注从"对着 PDF 从零敲真值"降级成"核对一份草稿"。
  · re-parse 报告 → 把解析输出当"真值候选" → 人只需确认/修正
  · 硬规则干净的 → 草稿大概率=真值 → 人用 show_provenance 红框秒确认
  · 硬规则红线的 → 标 needs_fix，人对着 PDF 改正确
seed 不创造真值；人的确认才创造真值（把 _status 改成 confirmed）。

格式与解析输出同构（revenue_breakdown.industries/segments/regions），
所以 src/eval/revenue_score.py 能直接拿它打分。

合并语义：重跑不覆盖人已确认(_status=confirmed*)的条目，只刷新 todo 条目。

用法：
  python3 -m scripts.seed_revenue_golden --codes 300005,300009
  python3 -m scripts.seed_revenue_golden --from test_results/baseline_60.json --clean-only --limit 30
  # 产出 goldset/revenue_golden.json → 人工把 _status 从 todo_* 改成 confirmed，并修正值
"""

import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.engine_orchestrator import FinParseAI
from src.validators.hard_rules import check_hard_rules

_DIMS = ("industries", "segments", "regions", "by_channel")
_OUT_DEFAULT = "goldset/revenue_golden.json"


def _pdf_path(code: str, year: int):
    cache = Config.PDF_CACHE_DIR
    hits = sorted(cache.glob(f"{code}_{year}_*.pdf")) if cache.exists() else []
    if not hits:
        hits = sorted(cache.glob(f"{code}_{year}.pdf")) if cache.exists() else []
    return str(hits[0]) if hits else None


def _select(args) -> list:
    """返回 [(code, year)]。"""
    out = []
    if args.codes:
        for c in args.codes.split(","):
            c = c.strip()
            if c:
                out.append((c, args.year))
    elif args.from_path:
        data = json.load(open(args.from_path, encoding="utf-8"))
        for r in data.get("records", []):
            if r.get("error"):
                continue
            if args.clean_only and not r.get("hard_passed"):
                continue
            out.append((r["stock_code"], r.get("year", args.year)))
    if args.limit > 0:
        out = out[: args.limit]
    return out


def _candidate(rb: dict) -> dict:
    """从解析输出抽出值级 golden 候选（只留维度行的 name/收入/占比）。"""
    cand = {}
    for d in _DIMS:
        rows = rb.get(d) or []
        if not rows:
            continue
        cand[d] = [{"name": r.get("name"),
                    "revenue_yuan": r.get("revenue_yuan"),
                    "ratio_pct": r.get("ratio_pct")} for r in rows]
    return cand


def _source_pages(result: dict) -> list:
    # engine 把溯源嵌在 output["溯源"]["revenue_breakdown"] 下（见 engine_orchestrator:85-87）
    prov = (result.get("溯源") or {}).get("revenue_breakdown") or {}
    pages = {v.get("page") for v in prov.values() if isinstance(v, dict) and v.get("page")}
    return sorted(p for p in pages if p)


def run(args):
    targets = _select(args)
    if not targets:
        print("❌ 没选到报告。用 --codes 或 --from。")
        return
    out_path = args.out
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # 合并：保留人已确认的条目
    existing = {}
    if os.path.exists(out_path):
        for e in json.load(open(out_path, encoding="utf-8")).get("entries", []):
            existing[(e["stock_code"], e["year"])] = e

    engine = FinParseAI()
    entries = dict(existing)
    kept, seeded, skipped = 0, 0, 0
    t0 = time.time()

    for i, (code, year) in enumerate(targets, 1):
        key = (code, year)
        if key in existing and str(existing[key].get("_status", "")).startswith("confirmed"):
            kept += 1
            print(f"  [{i}/{len(targets)}] {code} 已确认，跳过")
            continue
        pdf = _pdf_path(code, year)
        if not pdf:
            skipped += 1
            print(f"  [{i}/{len(targets)}] {code} 无缓存 PDF，跳过")
            continue
        try:
            r = engine.run(pdf, stock_code=code, report_year=year, db_write=False)
            rb = r.get("revenue_breakdown") or {}
            hard = check_hard_rules(r)
            rev_red = "revenue_breakdown" in (hard.get("red_fields") or [])
            entries[key] = {
                "stock_code": code, "year": year,
                "revenue_breakdown": _candidate(rb),
                "_status": "todo_confirm" if not rev_red else "todo_fix",
                "_hard": {"passed": hard["passed"], "revenue_red": rev_red},
                "_source_pages": _source_pages(r),
                "_seeded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            seeded += 1
            flag = "✅干净→待确认" if not rev_red else "❌红线→待修"
            ndim = len(entries[key]["revenue_breakdown"])
            print(f"  [{i}/{len(targets)}] {code} {flag}  {ndim}维 页{entries[key]['_source_pages']}")
        except Exception as e:
            skipped += 1
            print(f"  [{i}/{len(targets)}] {code} 💥 {str(e)[:80]}")

    payload = {
        "_instructions": "人工核对：把 _status 从 todo_confirm/todo_fix 改成 confirmed；"
                         "todo_fix 的要对照 PDF 修正 revenue_breakdown 里的值。"
                         "确认后的条目重跑 seed 不会被覆盖。"
                         "用 show_provenance 看红框可加速核对。",
        "field": "revenue_breakdown",
        "entries": sorted(entries.values(), key=lambda e: (e["stock_code"], e["year"])),
    }
    json.dump(payload, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    n = len(payload["entries"])
    n_conf = sum(1 for e in payload["entries"] if str(e.get("_status", "")).startswith("confirmed"))
    n_cf = sum(1 for e in payload["entries"] if e.get("_status") == "todo_confirm")
    n_fix = sum(1 for e in payload["entries"] if e.get("_status") == "todo_fix")
    print(f"\n{'='*56}")
    print(f"📋 golden: {out_path}  (耗时 {time.time()-t0:.0f}s)")
    print(f"  总条目 {n}：已确认 {n_conf} | 待确认 {n_cf} | 待修 {n_fix}")
    print(f"  本轮 seed {seeded}，保留已确认 {kept}，跳过 {skipped}")
    print(f"  下一步：人工把 todo_* 核对成 confirmed（show_provenance 看红框加速）")


def main():
    ap = argparse.ArgumentParser(description="营收值级 golden seed 器")
    ap.add_argument("--codes", type=str, help="逗号分隔股票代码")
    ap.add_argument("--from", dest="from_path", type=str, help="baseline 结果 json，取其中报告")
    ap.add_argument("--clean-only", action="store_true", help="配合 --from，只取硬规则干净的")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=str, default=_OUT_DEFAULT)
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
