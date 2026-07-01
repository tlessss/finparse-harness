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


# 注：A/B 表择优已从解析器上移到"选表解耦"(select_table 的锚+维度闸)，
# 解析器只负责"给定选中表→结构化"。这里改测新接缝：认列的占比闸门不把毛利率当占比。
def test_resolve_ratio_gate_rejects_margin():
    p = RevenueParser({})
    # 毛利率表:金额列仍认到营业收入(1)，但占比列必须为 None(绝不拿毛利率顶替占比)
    name_col, amount_col, ratio_col = p._resolve_columns(MARGIN)
    assert amount_col == 1
    assert ratio_col is None
    # → 分桶后毛利率不会被误当占比写进 ratio_pct
    result, _ = p._classify(MARGIN, unit_ratio=1)
    assert all(item["ratio_pct"] is None for item in result["segments"])


def test_resolve_takes_true_composition_ratio():
    p = RevenueParser({})
    # 占比构成表:占营业收入比重列正常认到(2)，分桶后 ratio_pct 有值
    name_col, amount_col, ratio_col = p._resolve_columns(COMPOSITION)
    assert (amount_col, ratio_col) == (1, 2)
    result, _ = p._classify(COMPOSITION, unit_ratio=1)
    assert [item["ratio_pct"] for item in result["segments"]] == [60.0, 40.0]


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
