"""指纹缩候选 candidates_for — 纯函数单测。"""

from src.eval.parser_catalog import candidates_for

_CAT = [
    {"key": "A", "path": "a.py", "fingerprints": ["fpA"]},
    {"key": "B", "path": "b.py", "fingerprints": ["fpB"]},
    {"key": "C", "path": "c.py", "fingerprints": []},
]


def test_narrow_by_fingerprint():
    assert [c["key"] for c in candidates_for("fpA", _CAT)] == ["A"]
    assert [c["key"] for c in candidates_for("fpB", _CAT)] == ["B"]


def test_fallback_when_fp_unknown():
    # 指纹没匹配 → 全跑兜底(召回,别漏对的)
    assert len(candidates_for("zzz", _CAT)) == 3


def test_fallback_when_no_fp():
    assert len(candidates_for("", _CAT)) == 3
    assert len(candidates_for(None, _CAT)) == 3
