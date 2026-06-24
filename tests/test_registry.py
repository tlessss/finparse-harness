"""注册表 + 选择即验证 测试（合成解析器，无需 PDF）"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.parsers.registry import ParserRegistry, ReportParser, score_result


class FakeParser(ReportParser):
    def __init__(self, key, result, fp_match=None):
        self.key = key
        self._result = result
        self._fp = fp_match

    def matches(self, fingerprint):
        return self._fp is not None and fingerprint == self._fp

    def parse(self, pdf_path, pre_scan, context=None):
        return dict(self._result)


# 干净结果：营收占比和=100（≥3 项，硬规则才会校验占比和）
CLEAN = {"revenue_breakdown": {"segments": [
    {"name": "A", "ratio_pct": 60}, {"name": "B", "ratio_pct": 30},
    {"name": "C", "ratio_pct": 10}]}, "field_count": 1}
# 脏结果：占比和=200（红线）
DIRTY = {"revenue_breakdown": {"segments": [
    {"name": "A", "ratio_pct": 100}, {"name": "B", "ratio_pct": 60},
    {"name": "C", "ratio_pct": 40}]}, "field_count": 1}


def test_score_clean_beats_dirty():
    assert score_result(CLEAN) > score_result(DIRTY)


def test_route_picks_clean_specialized():
    # 通用给脏结果，专用(匹配指纹)给干净 → 选专用
    reg = ParserRegistry(generic=FakeParser("generic", DIRTY))
    reg.register(FakeParser("spec", CLEAN, fp_match="fpX"))
    best = reg.route("x.pdf", pre_scan=[], fingerprint="fpX")
    assert best["_parser"] == "spec", best


def test_route_falls_back_to_generic():
    # 没有匹配的专用 → 用通用兜底
    reg = ParserRegistry(generic=FakeParser("generic", CLEAN))
    reg.register(FakeParser("spec", DIRTY, fp_match="other"))
    best = reg.route("x.pdf", pre_scan=[], fingerprint="fpX")
    assert best["_parser"] == "generic", best


def test_route_specialized_dirty_loses_to_generic_clean():
    # 专用匹配了但解得脏，通用解得干净 → 选择即验证选通用（不盲信指纹）
    reg = ParserRegistry(generic=FakeParser("generic", CLEAN))
    reg.register(FakeParser("spec", DIRTY, fp_match="fpX"))
    best = reg.route("x.pdf", pre_scan=[], fingerprint="fpX")
    assert best["_parser"] == "generic", best


def test_candidate_failure_isolated():
    class Boom(ReportParser):
        key = "boom"
        def matches(self, fp): return True
        def parse(self, *a, **k): raise RuntimeError("boom")
    reg = ParserRegistry(generic=FakeParser("generic", CLEAN))
    reg.register(Boom())
    best = reg.route("x.pdf", pre_scan=[], fingerprint="fpX")
    assert best["_parser"] == "generic"   # 崩溃候选被隔离，仍出结果


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
