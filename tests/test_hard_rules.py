"""硬规则单元测试 — 验证红线勾稽逻辑正确"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.validators.hard_rules import check_hard_rules


def test_clean_revenue_passes():
    r = check_hard_rules({"revenue_breakdown": {"segments": [
        {"name": "A", "ratio_pct": 60.0}, {"name": "B", "ratio_pct": 25.0},
        {"name": "C", "ratio_pct": 15.0},
    ]}})
    assert r["passed"], r
    assert r["red_count"] == 0


def test_revenue_sum_way_off_is_red():
    # 占比之和 250% → 明显错误（合计行混入/重复）
    r = check_hard_rules({"revenue_breakdown": {"segments": [
        {"name": "A", "ratio_pct": 100.0}, {"name": "B", "ratio_pct": 90.0},
        {"name": "C", "ratio_pct": 60.0},
    ]}})
    assert not r["passed"]
    assert r["red_count"] == 1


def test_illegal_ratio_is_red():
    r = check_hard_rules({"revenue_breakdown": {"segments": [
        {"name": "A", "ratio_pct": 150.0}, {"name": "B", "ratio_pct": 5.0},
        {"name": "C", "ratio_pct": 5.0},
    ]}})
    assert not r["passed"]
    assert any(v["rule"] == "ratio_range" for v in r["violations"])


def test_few_items_no_conclusion():
    # 只有 1 个分项，无法对占比之和下结论 → 不报 red
    r = check_hard_rules({"revenue_breakdown": {"segments": [
        {"name": "唯一产品", "ratio_pct": 100.0},
    ]}})
    assert r["passed"]


def test_rnd_sum_mismatch_is_red():
    r = check_hard_rules({"rnd_info": {
        "rnd_detail": [{"name": "薪酬", "amount_this": 100}, {"name": "材料", "amount_this": 50}],
        "total_this": 1000,  # 明细和=150，差异巨大
    }})
    assert not r["passed"]


def test_rnd_sum_match_passes():
    r = check_hard_rules({"rnd_info": {
        "rnd_detail": [{"name": "薪酬", "amount_this": 600}, {"name": "材料", "amount_this": 400}],
        "total_this": 1000,
    }})
    assert r["passed"]


def test_employee_mismatch_is_red():
    r = check_hard_rules({"employees": {
        "total": 1000,
        "composition": [{"type": "研发", "count": 300}, {"type": "生产", "count": 200}],
    }})
    assert not r["passed"]


def test_employee_match_passes():
    r = check_hard_rules({"employees": {
        "total": 500,
        "composition": [{"type": "研发", "count": 300}, {"type": "生产", "count": 200}],
    }})
    assert r["passed"]


def test_empty_result_passes():
    # 全空：硬规则无可校验项，不应报错
    assert check_hard_rules({})["passed"]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  ❌ {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
