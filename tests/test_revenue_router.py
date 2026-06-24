"""营收路由 — 运行时硬规则信号单测（纯函数，不碰 PDF）。"""

from src.parsers.revenue_router import revenue_plausibility


def test_clean_when_all_dims_sum_100():
    rb = {"industries": [{"name": "A", "revenue_yuan": 6, "ratio_pct": 60},
                          {"name": "B", "revenue_yuan": 4, "ratio_pct": 40}]}
    s = revenue_plausibility(rb)
    assert s["clean"] is True
    assert s["ratio_ok_dims"] == 1 and s["rows"] == 2


def test_dirty_when_ratio_sum_off():
    rb = {"industries": [{"name": "A", "revenue_yuan": 6, "ratio_pct": 60},
                          {"name": "B", "revenue_yuan": 2, "ratio_pct": 19}]}  # 和=79
    assert revenue_plausibility(rb)["clean"] is False


def test_empty_not_clean():
    assert revenue_plausibility({})["clean"] is False
    assert revenue_plausibility(None)["clean"] is False


def test_partial_dim_drags_clean_false():
    rb = {"industries": [{"name": "A", "revenue_yuan": 10, "ratio_pct": 100}],
          "segments": [{"name": "X", "revenue_yuan": 5, "ratio_pct": 50}]}  # segments 和=50
    s = revenue_plausibility(rb)
    assert s["clean"] is False and s["ratio_ok_dims"] == 1 and s["n_dims"] == 2
