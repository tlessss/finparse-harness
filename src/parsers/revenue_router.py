"""
营收解析器路由 — 选择即验证（运行时，无 golden，用硬规则当信号）

新报告来了 → 在已认证营收解析器里跑一遍 → 谁解出来"硬规则干净"(各维度占比和≈100)就用谁。
没有合适的 → needs_repair（交给冷启动/生成 fork/新建，见 agents/code_generator.repair）。

与 eval/parser_catalog.pick_mother 的区别：
  pick_mother 用 golden 打分（构建/认证期，有真值）；
  本路由用硬规则代理（生产期，新报告无 golden）。
指纹缩候选是可选预筛(召回)，最终对不对交给硬规则——见 docs/多agent编排设计.md §三。
"""

from typing import Dict, List, Optional

from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import version_parse_fn
from src.eval.parser_catalog import load_certified

_DIMS = ("industries", "segments", "regions", "by_channel")


def revenue_plausibility(rb: Optional[Dict]) -> Dict:
    """运行时硬规则信号：各维度占比和是否≈100。无 golden 时判"解得对不对"的代理。"""
    rb = rb or {}
    dims = [d for d in _DIMS if rb.get(d)]
    if not dims:
        return {"clean": False, "ratio_ok_dims": 0, "n_dims": 0, "rows": 0}
    ok, rows = 0, 0
    for d in dims:
        ratios = [r.get("ratio_pct") for r in rb[d] if r.get("ratio_pct") is not None]
        rows += len(rb[d])
        if ratios and 97 <= sum(ratios) <= 103:
            ok += 1
    return {"clean": ok == len(dims) and rows >= 2,
            "ratio_ok_dims": ok, "n_dims": len(dims), "rows": rows}


def route_revenue(code: str, year: int,
                  catalog: List[Dict] = None, fingerprint: str = "") -> Dict:
    """
    选择即验证路由。返回:
      {"status": "routed"|"needs_repair",
       "parser": path|None, "parser_key": key|None,
       "result": revenue_breakdown|None, "signal": {...}, "tried": [(key, clean), ...]}
    """
    catalog = catalog if catalog is not None else load_certified()
    if get_tables(code, year) is None:
        return {"status": "needs_repair", "parser": None, "parser_key": None,
                "result": None, "signal": None, "tried": [], "reason": "无缓存表"}

    # 指纹缩候选（可选预筛；现 1 个认证解析器，全跑）
    cands = catalog  # TODO: 用 fingerprint 过滤 catalog

    best = None
    tried = []
    for c in cands:
        try:
            rb = version_parse_fn(c["path"])(code, year)
            sig = revenue_plausibility(rb)
        except Exception:
            rb, sig = None, {"clean": False, "ratio_ok_dims": 0, "n_dims": 0, "rows": 0}
        tried.append((c["key"], sig["clean"]))
        # 排序键：干净优先 → 占比达标维度多 → 行多
        key = (sig["clean"], sig["ratio_ok_dims"], sig["rows"])
        if best is None or key > best[0]:
            best = (key, c, rb, sig)

    if best and best[3]["clean"]:
        _, c, rb, sig = best
        return {"status": "routed", "parser": c["path"], "parser_key": c["key"],
                "result": rb, "signal": sig, "tried": tried}
    return {"status": "needs_repair", "parser": None, "parser_key": None,
            "result": None, "signal": (best[3] if best else None), "tried": tried,
            "reason": "无认证解析器硬规则达标 → 冷启动/生成"}
