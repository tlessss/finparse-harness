"""字段通用 — 同一套打分器/plausibility 处理成本(扁平列表,amount_yuan)。"""
from src.eval.field_spec import COST, REVENUE, as_dims
from src.eval.revenue_score import score_field
from src.parsers.revenue_router import field_plausibility

_COST_GOLD = [  # 成本=扁平列表
    {"name": "原材料", "amount_yuan": 600.0, "ratio_pct": 60.0},
    {"name": "直接人工", "amount_yuan": 400.0, "ratio_pct": 40.0},
]


def test_as_dims_flat_list_wraps():
    assert list(as_dims(_COST_GOLD, COST).keys()) == ["_all"]
    assert set(as_dims({"industries": [1]}, REVENUE).keys()) >= {"industries"}


def test_cost_score_exact():
    s = score_field(COST, _COST_GOLD, _COST_GOLD)
    assert s["exact"] is True and s["score"] == 1.0


def test_cost_score_missing_row():
    s = score_field(COST, _COST_GOLD[:1], _COST_GOLD)
    assert not s["exact"]
    assert any("漏行" in m.get("issue", "") for m in s["mismatches"])


def test_cost_plausibility_sum100():
    assert field_plausibility(COST, _COST_GOLD)["clean"] is True


def test_cost_plausibility_dirty():
    bad = [{"name": "A", "amount_yuan": 1, "ratio_pct": 60},
           {"name": "B", "amount_yuan": 1, "ratio_pct": 20}]  # 和80
    assert field_plausibility(COST, bad)["clean"] is False


# ── B类(研发：明细和≈合计) ──
from src.eval.field_spec import RND

_RND_GOLD = {"total_this": 1000.0, "rnd_detail": [
    {"name": "职工薪酬", "amount_this": 600.0},
    {"name": "研发材料", "amount_this": 400.0},
]}


def test_rnd_score_exact():
    s = score_field(RND, _RND_GOLD, _RND_GOLD)
    assert s["exact"] is True and s["score"] == 1.0


def test_rnd_wrong_total_flagged():
    bad = {"total_this": 1500.0, "rnd_detail": _RND_GOLD["rnd_detail"]}   # 明显偏离
    s = score_field(RND, bad, _RND_GOLD)
    assert not s["exact"]
    assert any(m.get("issue") == "合计不符" for m in s["mismatches"])


def test_rnd_plausibility_sum_eq_total():
    assert field_plausibility(RND, _RND_GOLD)["clean"] is True


def test_rnd_plausibility_dirty():
    bad = {"total_this": 1000.0, "rnd_detail": [
        {"name": "A", "amount_this": 600.0}, {"name": "B", "amount_this": 100.0}]}  # 和700≠1000
    assert field_plausibility(RND, bad)["clean"] is False
