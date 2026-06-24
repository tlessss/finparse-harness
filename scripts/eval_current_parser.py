"""
点火脚本 — 把现有解析器当 v0，对着 confirmed golden 打分

验证地基第一次"跑活"：现有解析器 = 基线版本 v0，
对每条已确认 golden 解析 → 打分器评分 → 看 v0 基线分 + 逐份差异。
将来 LLM 写的 v1 就拿 accept_candidate 和这个 v0 比。

用法：
  python3 -m scripts.eval_current_parser
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.engine_orchestrator import FinParseAI
from src.eval.run_eval import load_golden, eval_version

_engine = None


def parse_fn(code: str, year: int):
    """v0 = 现有解析器。(code,year) -> revenue_breakdown | None"""
    global _engine
    if _engine is None:
        _engine = FinParseAI()
    hits = sorted(Config.PDF_CACHE_DIR.glob(f"{code}_{year}*.pdf"))
    if not hits:
        return None
    r = _engine.run(str(hits[0]), stock_code=code, report_year=year, db_write=False)
    return r.get("revenue_breakdown")


def main():
    gold = load_golden()
    confirmed = [e for e in gold if str(e.get("_status", "")).startswith("confirmed")]
    print(f"📋 golden 共 {len(gold)} 条，已确认 {len(confirmed)} 条")
    if not confirmed:
        print("❌ 没有 confirmed golden，先把 _status 改成 confirmed。")
        return

    res = eval_version(parse_fn, gold)            # eval_version 内部只取 confirmed
    s = res["summary"]
    print(f"\n{'='*60}\n🔥 v0(现有解析器) 基线分")
    print(f"   评估 {s['n']} 份 | exact {s['exact']} | 均分 {s['mean_score']} | 报错 {s['errored']}")
    print(f"{'='*60}")
    for r in res["per_report"]:
        tag = "✅exact" if r.get("exact") else f"分={r['score']}"
        print(f"  {r['stock_code']} {r['year']}: {tag}")
        for m in (r.get("mismatches") or [])[:6]:
            print(f"      ✗ [{m.get('dim')}] {m.get('name')}: {m.get('issue')}")
        if r.get("error"):
            print(f"      💥 {r['error']}")


if __name__ == "__main__":
    main()
