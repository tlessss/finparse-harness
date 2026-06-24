"""多版本评估 harness 单测 — parse_fn 用假的，不碰 PDF。"""

from src.eval.run_eval import eval_version, compare_versions, best_version_per_report, _confirmed

_GOLD = [
    {"stock_code": "300005", "year": 2025, "_status": "confirmed",
     "revenue_breakdown": {"industries": [
         {"name": "户外业务", "revenue_yuan": 1150774808.79, "ratio_pct": 83.35},
         {"name": "芯片业务", "revenue_yuan": 229938287.60, "ratio_pct": 16.65}]}},
    {"stock_code": "999999", "year": 2025, "_status": "todo_confirm",   # 未确认 → 不该评
     "revenue_breakdown": {"industries": [{"name": "X", "revenue_yuan": 1, "ratio_pct": 100}]}},
]


def _perfect(code, year):
    return {"industries": [
        {"name": "户外业务", "revenue_yuan": 1150774808.79, "ratio_pct": 83.35},
        {"name": "芯片业务", "revenue_yuan": 229938287.60, "ratio_pct": 16.65}]}


def _missing(code, year):
    return {"industries": [{"name": "户外业务", "revenue_yuan": 1150774808.79, "ratio_pct": 83.35}]}


def _broken(code, year):
    raise RuntimeError("解析炸了")


def test_only_confirmed_evaluated():
    assert len(_confirmed(_GOLD)) == 1     # 未确认的被排除


def test_perfect_version_exact():
    r = eval_version(_perfect, _GOLD)
    assert r["summary"]["n"] == 1          # 只评了那条 confirmed
    assert r["summary"]["exact"] == 1
    assert r["summary"]["mean_score"] == 1.0


def test_missing_version_scores_low():
    r = eval_version(_missing, _GOLD)
    assert r["summary"]["exact"] == 0
    assert r["per_report"][0]["score"] < 1.0


def test_broken_version_counted_as_error():
    r = eval_version(_broken, _GOLD)
    assert r["summary"]["errored"] == 1
    assert r["per_report"][0]["score"] == 0.0


def test_compare_picks_best():
    cmp = compare_versions({"perfect": _perfect, "missing": _missing}, _GOLD)
    assert cmp["leaderboard"][0][0] == "perfect"        # 排行榜冠军
    best = best_version_per_report({"perfect": _perfect, "missing": _missing}, _GOLD)
    assert best[("300005", 2025)] == "perfect"          # 每份选优
