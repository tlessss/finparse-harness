"""沙箱测试 — 验证 accept/rollback 比较逻辑（纯函数，无需 PDF）"""

import os
import sys
_LEGACY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _LEGACY)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "."))

from sandbox import fix_outcome, run_sandbox


def H(passed, red, fields=None):
    return {"passed": passed, "red_count": red, "red_fields": fields or []}


def test_fail_to_pass_accepts():
    assert fix_outcome(H(False, 2, ["revenue_breakdown"]), H(True, 0)) == "accept"


def test_fewer_reds_accepts():
    assert fix_outcome(H(False, 3, ["a", "b"]), H(False, 1, ["a"])) == "accept"


def test_no_change_rejects():
    assert fix_outcome(H(False, 2, ["a"]), H(False, 2, ["a"])) == "reject"


def test_regression_rejects():
    assert fix_outcome(H(True, 0), H(False, 1, ["a"])) == "reject"


def test_new_red_field_rejects():
    # red 数没增但换了字段引入新红线 → 拒绝
    assert fix_outcome(H(False, 2, ["a"]), H(False, 1, ["b"])) == "reject"


def test_run_sandbox_picks_best():
    # 注入假 parse_fn：规则里带 'fix' 标记的解析结果更干净
    def parse_fn(rule):
        return {"_q": rule.get("q", 0)}

    def validator(pr):
        q = pr["_q"]
        return H(q >= 2, max(0, 3 - q), ["x"] if q < 3 else [])

    base = {"q": 0}
    cands = [{"q": 1}, {"q": 3}, {"q": 2}]
    out = run_sandbox(parse_fn, base, cands, validator=validator)
    assert out["accepted"]
    assert out["best"]["rule"]["q"] == 3   # 红线最少者胜出
    assert out["before"]["red_count"] == 3


def test_run_sandbox_all_reject():
    def parse_fn(rule):
        return {}

    def validator(pr):
        return H(False, 2, ["x"])   # 候选都不变好

    out = run_sandbox(parse_fn, {"b": 1}, [{"c": 1}, {"c": 2}], validator=validator)
    assert not out["accepted"] and out["best"] is None


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
