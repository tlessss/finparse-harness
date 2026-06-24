"""版式指纹测试 — 用真实缓存 PDF 验证 doc_type 判定与指纹稳定性"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.parsers.infra.layout_fingerprint import compute_fingerprint, _detect_doc_type

CACHE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..",
                     "book-agent", "output", "pdf_cache")


def test_detect_doc_type_unit():
    # 普通公司现金流量表里的银行类科目不应误判为银行
    assert _detect_doc_type("吸收存款 发放贷款 营业收入") == "normal"
    # 银行特有科目重复出现才判银行
    assert _detect_doc_type("非利息净收入 非利息净收入 利息净收入 利息净收入 利息净收入") == "bank"
    assert _detect_doc_type("已赚保费 已赚保费 退保金") == "insurance"


def test_fingerprint_stable():
    f = os.path.join(CACHE, "000002_2025_a953e1fae21e.pdf")
    if not os.path.exists(f):
        print("  (skip: 000002 缓存缺失)")
        return
    a = compute_fingerprint(f)
    b = compute_fingerprint(f)
    assert a["hash"] == b["hash"], "同一文件指纹应稳定"
    assert a["doc_type"] == "normal"


def test_fingerprint_bad_path():
    fp = compute_fingerprint("/nonexistent.pdf")
    assert fp["doc_type"] == "unknown" and fp["hash"] == "unknown"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn(); print(f"  ✅ {fn.__name__}"); passed += 1
        except Exception:
            print(f"  ❌ {fn.__name__}"); traceback.print_exc(); failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
