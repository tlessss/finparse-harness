"""打分器单测 — 纯函数，不碰 PDF。"""

from src.eval.revenue_score import score_revenue, score_dimension


_GOLD = {
    "industries": [
        {"name": "户外业务", "revenue_yuan": 1150774808.79, "ratio_pct": 83.35},
        {"name": "芯片业务", "revenue_yuan": 229938287.60, "ratio_pct": 16.65},
    ],
}


def test_exact_match():
    s = score_revenue(_GOLD, _GOLD)
    assert s["exact"] is True
    assert s["score"] == 1.0
    assert s["mismatches"] == []


def test_missing_row_hurts_recall():
    pred = {"industries": _GOLD["industries"][:1]}   # 漏了芯片业务
    s = score_revenue(pred, _GOLD)
    assert not s["exact"]
    assert s["per_dim"]["industries"]["row_recall"] == 0.5
    assert any(m["issue"].startswith("漏行") for m in s["mismatches"])


def test_extra_row_hurts_precision():
    pred = {"industries": _GOLD["industries"] + [{"name": "合计", "revenue_yuan": 1, "ratio_pct": 100}]}
    s = score_revenue(pred, _GOLD)
    assert not s["exact"]
    assert s["precision"] < 1.0


def test_wrong_value_flagged():
    pred = {"industries": [
        {"name": "户外业务", "revenue_yuan": 999.0, "ratio_pct": 83.35},   # 收入错
        {"name": "芯片业务", "revenue_yuan": 229938287.60, "ratio_pct": 16.65},
    ]}
    s = score_revenue(pred, _GOLD)
    assert not s["exact"]
    bad = [m for m in s["mismatches"] if m["issue"] == "值不符"]
    assert bad and bad[0]["收入"] is not None


def test_tolerance_allows_rounding():
    pred = {"industries": [
        {"name": "户外业务", "revenue_yuan": 1150774808.79 * 1.005, "ratio_pct": 83.1},
        {"name": "芯片业务", "revenue_yuan": 229938287.60, "ratio_pct": 16.65},
    ]}
    s = score_revenue(pred, _GOLD)  # 收入±0.5%、占比±0.25pp 都在容差内
    assert s["exact"] is True


def test_unlabeled_dim_not_penalized():
    # golden 只标了 industries；输出多给了 segments，不该扣分（该维度没标）
    pred = {"industries": _GOLD["industries"],
            "segments": [{"name": "X", "revenue_yuan": 1, "ratio_pct": 1}]}
    s = score_revenue(pred, _GOLD)
    assert s["exact"] is True
    assert s["evaluated_dims"] == 1
