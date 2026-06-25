"""事后自动溯源 attach_provenance — 纯函数单测。"""
from src.eval.provenance import attach_provenance

_TABLES = [{
    "page": 19,
    "table": [["起重机械", "20,982,874,717.22", "20.81%"]],
    "cell_bbox": [[[0, 0, 10, 5], [10, 0, 30, 5], [30, 0, 45, 5]]],
}]
_RB = {"segments": [{"name": "起重机械", "revenue_yuan": 20982874717.22, "ratio_pct": 20.81}]}


def test_attach_matches_cells():
    p = attach_provenance(_RB, _TABLES)
    assert p["segments[0].name"]["page"] == 19
    assert p["segments[0].revenue_yuan"]["bbox"] == [10, 0, 30, 5]
    assert p["segments[0].ratio_pct"]["bbox"] == [30, 0, 45, 5]


def test_no_match_returns_empty():
    p = attach_provenance({"segments": [{"name": "查无此名", "revenue_yuan": 1, "ratio_pct": 1}]}, _TABLES)
    assert p == {}
