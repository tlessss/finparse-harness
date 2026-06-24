"""多版本评估 harness 单测 — parse_fn 用假的，不碰 PDF。"""

from src.eval.run_eval import (eval_version, compare_versions, best_version_per_report,
                               accept_candidate, _confirmed)

# 两份真值（300005 两行 / 300009 一行）
_T05 = {"industries": [
    {"name": "户外业务", "revenue_yuan": 1150774808.79, "ratio_pct": 83.35},
    {"name": "芯片业务", "revenue_yuan": 229938287.60, "ratio_pct": 16.65}]}
_T09 = {"industries": [
    {"name": "生物制品", "revenue_yuan": 1979905736.96, "ratio_pct": 74.81}]}

_GOLD = [
    {"stock_code": "300005", "year": 2025, "_status": "confirmed", "revenue_breakdown": _T05},
    {"stock_code": "300009", "year": 2025, "_status": "confirmed", "revenue_breakdown": _T09},
    {"stock_code": "999999", "year": 2025, "_status": "todo_confirm",   # 未确认 → 不评
     "revenue_breakdown": {"industries": [{"name": "X", "revenue_yuan": 1, "ratio_pct": 100}]}},
]


def _perfect(code, year):
    return {"300005": _T05, "300009": _T09}[code]


def _miss05(code, year):   # 300009 对，300005 漏一行
    return {"300005": {"industries": _T05["industries"][:1]}, "300009": _T09}[code]


def _broken(code, year):
    raise RuntimeError("解析炸了")


def test_only_confirmed_evaluated():
    assert len(_confirmed(_GOLD)) == 2     # 未确认的被排除


def test_perfect_version_exact():
    r = eval_version(_perfect, _GOLD)
    assert r["summary"]["n"] == 2
    assert r["summary"]["exact"] == 2
    assert r["summary"]["mean_score"] == 1.0


def test_missing_version_scores_low():
    r = eval_version(_miss05, _GOLD)
    assert r["summary"]["exact"] == 1                  # 只有 300009 完美
    s05 = next(x for x in r["per_report"] if x["stock_code"] == "300005")
    assert s05["score"] < 1.0


def test_broken_version_counted_as_error():
    r = eval_version(_broken, _GOLD)
    assert r["summary"]["errored"] == 2
    assert all(x["score"] == 0.0 for x in r["per_report"])


def test_compare_picks_best():
    cmp = compare_versions({"perfect": _perfect, "miss05": _miss05}, _GOLD)
    assert cmp["leaderboard"][0][0] == "perfect"
    best = best_version_per_report({"perfect": _perfect, "miss05": _miss05}, _GOLD)
    assert best[("300005", 2025)] == "perfect"


def test_accept_rejects_regression():
    # base 全对；candidate 把 300005 改坏 → 必须拒（正确率优先，不改坏别的）
    out = accept_candidate(_perfect, _miss05, _GOLD)
    assert out["accepted"] is False
    assert ("300005", 2025) in out["regressions"]


def test_accept_accepts_improvement():
    # base 漏 300005；candidate 全对 → 提升且不退步 → 收
    out = accept_candidate(_miss05, _perfect, _GOLD)
    assert out["accepted"] is True
    assert out["regressions"] == []
    assert out["candidate_score"] > out["base_score"]
