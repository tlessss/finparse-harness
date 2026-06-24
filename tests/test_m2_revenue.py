"""M2 测试 — A/B 表择优 + 销售模式维度 + 分表校验（无需 PDF）"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.parsers.revenue.default import RevenueParser
from src.validators.hard_rules import check_hard_rules

# (A) 占比构成表
COMPOSITION = [
    ["分产品", "营业收入", "占营业收入比重"],
    ["产品A", "600", "60%"],
    ["产品B", "400", "40%"],
]
# (B) 毛利率表
MARGIN = [
    ["分产品", "营业收入", "营业成本", "毛利率"],
    ["产品A", "600", "400", "33%"],
    ["产品B", "400", "300", "25%"],
]


def test_select_prefers_composition():
    p = RevenueParser({})
    # 候选里同时有 B 和 A（B 排前）→ 应择优选 A 占比表
    assert p._select_best_table([MARGIN, COMPOSITION]) is COMPOSITION


def test_select_falls_back_to_margin_only():
    p = RevenueParser({})
    # 只有 B → 退回 B（不会凭空造占比）
    assert p._select_best_table([MARGIN]) is MARGIN


def test_by_channel_dimension_validated():
    # 销售模式维度占比之和也走硬规则
    bad = check_hard_rules({"revenue_breakdown": {"by_channel": [
        {"name": "直销", "ratio_pct": 50.0},
        {"name": "经销", "ratio_pct": 30.0},
        {"name": "其他", "ratio_pct": 5.0},   # 和=85% → 红线
    ]}})
    assert not bad["passed"]
    good = check_hard_rules({"revenue_breakdown": {"by_channel": [
        {"name": "直销", "ratio_pct": 60.0},
        {"name": "经销", "ratio_pct": 30.0},
        {"name": "其他", "ratio_pct": 10.0},
    ]}})
    assert good["passed"]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print(f"  ✅ {fn.__name__}"); p += 1
        except Exception:
            print(f"  ❌ {fn.__name__}"); traceback.print_exc(); f += 1
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
