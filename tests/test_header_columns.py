"""表头驱动认列测试 — 用合成表验证 M1 核心修复（无需 PDF）"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.parsers.infra.header_columns import detect_columns_by_header, classify_revenue_table
from src.parsers.infra.rule_loader import load_rule

RULE = load_rule("revenue")["revenue_breakdown"]
ALIASES = RULE["header_aliases"]

# 多氟多式：占比构成表，含两个年度的占比列
COMPOSITION = [
    ["分行业", "营业收入\n金额", "占营业收入比重", "金额(2024)", "占营业收入比重"],  # 表头(两年)
    ["工业",   "7987",          "84.67%",         "6304",        "76.43%"],
    ["其他",   "1446",          "15.33%",         "1944",        "23.57%"],
]

# 徐工式：收入/成本/毛利率表，没有"占营业收入比重"
MARGIN = [
    ["分产品", "营业收入", "营业成本", "毛利率", "毛利率比上年"],
    ["起重机械", "209",    "162",     "22.54%", "0.19%"],
    ["土方机械", "301",    "222",     "26.23%", "0.03%"],
]


def test_composition_picks_current_year_ratio():
    cols = detect_columns_by_header(COMPOSITION, ALIASES)
    # 占比列应锁定当年(第2列)，不是2024的第4列
    assert cols["ratio"] == 2, cols
    assert cols["revenue"] == 1, cols
    assert cols["name"] == 0, cols


def test_margin_table_ratio_is_none():
    # 关键修复：徐工式表没有"占营业收入比重"表头 → ratio 必须为 None（不拿毛利率顶替）
    cols = detect_columns_by_header(MARGIN, ALIASES)
    assert cols["ratio"] is None, f"毛利率被误当占比了: {cols}"
    assert cols["gross"] == 3, cols          # 毛利率应被识别为 gross
    assert cols["revenue"] == 1, cols


def test_classify_table_types():
    assert classify_revenue_table(COMPOSITION, RULE) == "composition"
    assert classify_revenue_table(MARGIN, RULE) == "margin"


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
