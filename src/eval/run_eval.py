"""
多解析器 × 多版本 评估 harness — 验证地基最后一块

把"跑某解析器某版本 over golden → 打分 → 选最优版本"串起来。
parse_fn 注入（不耦合解析怎么发生：可 re-parse、可跑缓存表、可跑 LLM 生成的版本），
所以本模块纯编排 + 打分，不碰 PDF，可脱环境单测。

对接（docs/多agent编排设计.md）：
  · 沙箱：LLM 改出新版本 → eval_version 打分 → 比基线版本高才 accept
  · registry 选优：best_version_per_report 给每份/每版式挑当前最优版本
  · 只用 _status=confirmed* 的 golden 当真值（未确认的不冤枉也不背书）

用法：
  from src.eval.run_eval import load_golden, eval_version, compare_versions
  gold = load_golden()
  r = eval_version(lambda code, year: parse_revenue(code, year), gold)
  r["summary"]  # {n, exact, mean_score, ...}
"""

import json
import os
from typing import Callable, Dict, List, Optional

from src.eval.revenue_score import score_revenue, _DIMS

_GOLDEN_DEFAULT = "goldset/revenue_golden.json"

# parse_fn: (stock_code, year) -> revenue_breakdown dict | None
ParseFn = Callable[[str, int], Optional[Dict]]


def load_golden(path: str = _GOLDEN_DEFAULT) -> List[Dict]:
    if not os.path.exists(path):
        return []
    return json.load(open(path, encoding="utf-8")).get("entries", [])


def _confirmed(entries: List[Dict]) -> List[Dict]:
    return [e for e in entries if str(e.get("_status", "")).startswith("confirmed")]


def eval_version(parse_fn: ParseFn, golden_entries: List[Dict],
                 dims=_DIMS, only_confirmed: bool = True) -> Dict:
    """
    用一个解析器版本跑遍 golden，逐份打分 + 汇总。

    Returns:
      {"summary": {n, exact, mean_score, errored},
       "per_report": [{stock_code, year, score, exact, mismatches?|error?}, ...]}
    """
    entries = _confirmed(golden_entries) if only_confirmed else list(golden_entries)
    per_report = []
    for e in entries:
        code, year = e["stock_code"], e["year"]
        try:
            pred = parse_fn(code, year)
        except Exception as ex:
            per_report.append({"stock_code": code, "year": year,
                               "score": 0.0, "exact": False, "error": str(ex)[:120]})
            continue
        s = score_revenue(pred, e.get("revenue_breakdown"), dims)
        per_report.append({"stock_code": code, "year": year,
                           "score": s["score"], "exact": s["exact"],
                           "mismatches": s["mismatches"]})
    n = len(per_report)
    scored = [r for r in per_report if "error" not in r]
    summary = {
        "n": n,
        "exact": sum(1 for r in per_report if r.get("exact")),
        "mean_score": round(sum(r["score"] for r in per_report) / n, 4) if n else 0.0,
        "errored": sum(1 for r in per_report if "error" in r),
        "evaluated": len(scored),
    }
    return {"summary": summary, "per_report": per_report}


def compare_versions(versions: Dict[str, ParseFn], golden_entries: List[Dict],
                     dims=_DIMS) -> Dict:
    """
    多个版本各跑 golden，出排行榜 + 每份的最优版本。

    versions: {版本名: parse_fn}
    Returns:
      {"leaderboard": [(版本名, mean_score, exact), ...降序],
       "results": {版本名: eval_version结果},
       "per_report_best": {(code,year): 版本名}}   # registry 选优依据
    """
    results = {name: eval_version(fn, golden_entries, dims) for name, fn in versions.items()}

    leaderboard = sorted(
        ((name, r["summary"]["mean_score"], r["summary"]["exact"]) for name, r in results.items()),
        key=lambda x: (-x[1], -x[2]),
    )

    # 每份报告挑得分最高的版本
    per_report_best = {}
    scores_by_report: Dict = {}
    for name, r in results.items():
        for rep in r["per_report"]:
            key = (rep["stock_code"], rep["year"])
            scores_by_report.setdefault(key, []).append((name, rep["score"]))
    for key, lst in scores_by_report.items():
        per_report_best[key] = max(lst, key=lambda x: x[1])[0]

    return {"leaderboard": leaderboard, "results": results,
            "per_report_best": per_report_best}


def best_version_per_report(versions: Dict[str, ParseFn], golden_entries: List[Dict],
                            dims=_DIMS) -> Dict:
    """registry 选优：每份 golden 报告 → 当前最优版本名。"""
    return compare_versions(versions, golden_entries, dims)["per_report_best"]


def accept_candidate(base_fn: ParseFn, candidate_fn: ParseFn, golden_entries: List[Dict],
                     dims=_DIMS, min_gain: float = 1e-6) -> Dict:
    """
    版本闸（沙箱/LLM 闭环的直接接口）：LLM 改出 candidate，要不要收？

    收的条件（正确率优先：宁可不收，绝不把别的改坏）：
      · 整体均分严格提升（> min_gain）
      · 且不在任何一份 golden 上比 base 退步

    Returns:
      {"accepted": bool, "base_score": float, "candidate_score": float,
       "regressions": [(code,year), ...], "improved": bool}
    """
    base = eval_version(base_fn, golden_entries, dims)
    cand = eval_version(candidate_fn, golden_entries, dims)
    base_by = {(r["stock_code"], r["year"]): r["score"] for r in base["per_report"]}

    regressions = []
    for r in cand["per_report"]:
        key = (r["stock_code"], r["year"])
        if key in base_by and r["score"] < base_by[key] - 1e-9:
            regressions.append(key)

    improved = cand["summary"]["mean_score"] > base["summary"]["mean_score"] + min_gain
    return {
        "accepted": improved and not regressions,
        "base_score": base["summary"]["mean_score"],
        "candidate_score": cand["summary"]["mean_score"],
        "improved": improved,
        "regressions": regressions,
    }
