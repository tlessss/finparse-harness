"""
字段解析器路由 — 选择即验证（运行时无 golden，用硬规则当信号）+ 指纹缩候选 + 缓存路由

字段通用：按 FieldSpec 取该字段的"解对没"硬规则信号(A类=各维度占比和≈100)。
新报告 → 在该字段已认证解析器里跑 → 谁硬规则干净就用谁；没有 → needs_repair。

与 parser_catalog.pick_mother 的区别：pick_mother 用 golden(认证期)；本路由用硬规则(生产期)。
"""

import os
from typing import Dict, List, Optional

from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import version_parse_fn
from src.eval.field_spec import FieldSpec, REVENUE, as_dims
from src.eval.parser_catalog import candidates_for, tag_fingerprint
from src.eval.route_index import fingerprint_of, route_get, route_set, route_invalidate


def field_plausibility(spec: FieldSpec, value) -> Dict:
    """A类(占比构成)运行时硬规则信号：各维度占比和≈100。无 golden 时判"解对没"的代理。"""
    dd = as_dims(value, spec)
    dims = [d for d in dd if dd[d]]
    if not dims:
        return {"clean": False, "ratio_ok_dims": 0, "n_dims": 0, "rows": 0}
    ok, rows = 0, 0
    for d in dims:
        ratios = [r.get(spec.ratio_key) for r in dd[d] if r.get(spec.ratio_key) is not None]
        rows += len(dd[d])
        if ratios and 97 <= sum(ratios) <= 103:
            ok += 1
    return {"clean": ok == len(dims) and rows >= 2,
            "ratio_ok_dims": ok, "n_dims": len(dims), "rows": rows}


def revenue_plausibility(rb: Optional[Dict]) -> Dict:
    """营收便捷入口。"""
    return field_plausibility(REVENUE, rb)


def route_field(spec: FieldSpec, code: str, year: int,
                catalog: List[Dict] = None, fingerprint: str = None) -> Dict:
    """
    字段通用选择即验证路由（指纹缩候选 + 缓存路由）。返回:
      {"status": "routed"|"needs_repair", "parser", "parser_key", "result",
       "signal", "tried", "fingerprint", "cache_hit", "candidates", "field"}
    传 catalog 时走纯候选(测试用)，不碰缓存/指纹索引。
    """
    field = spec.field
    base = {"field": field}
    if get_tables(code, year) is None:
        return {"status": "needs_repair", "parser": None, "parser_key": None,
                "result": None, "signal": None, "tried": [], "reason": "无缓存表", **base}

    use_index = catalog is None
    fp = fingerprint if fingerprint is not None else (fingerprint_of(code, year) if use_index else None)

    # ① 缓存命中：直接跑那一个，硬规则守门（漂移则失效重选）
    if use_index and fp:
        cached = route_get(field, fp)
        if cached and os.path.exists(cached):
            try:
                rb = version_parse_fn(cached)(code, year)
                sig = field_plausibility(spec, rb)
            except Exception:
                rb, sig = None, {"clean": False}
            if sig.get("clean"):
                return {"status": "routed", "parser": cached, "parser_key": "(缓存路由)",
                        "result": rb, "signal": sig, "tried": [], "fingerprint": fp,
                        "cache_hit": True, "candidates": 1, **base}
            route_invalidate(field, fp)

    # ② 指纹缩候选 → 跑验证选优
    cands = candidates_for(field, fp, catalog)
    best, tried = None, []
    for c in cands:
        try:
            rb = version_parse_fn(c["path"])(code, year)
            sig = field_plausibility(spec, rb)
        except Exception:
            rb, sig = None, {"clean": False, "ratio_ok_dims": 0, "n_dims": 0, "rows": 0}
        tried.append((c["key"], sig["clean"]))
        key = (sig["clean"], sig["ratio_ok_dims"], sig["rows"])
        if best is None or key > best[0]:
            best = (key, c, rb, sig)

    if best and best[3]["clean"]:
        _, c, rb, sig = best
        if use_index and fp:
            route_set(field, fp, c["path"])
            tag_fingerprint(c["path"], fp)
        return {"status": "routed", "parser": c["path"], "parser_key": c["key"],
                "result": rb, "signal": sig, "tried": tried, "fingerprint": fp,
                "cache_hit": False, "candidates": len(cands), **base}
    return {"status": "needs_repair", "parser": None, "parser_key": None,
            "result": None, "signal": (best[3] if best else None), "tried": tried,
            "fingerprint": fp, "candidates": len(cands),
            "reason": "无认证解析器硬规则达标 → 冷启动/生成", **base}


def route_revenue(code: str, year: int, catalog: List[Dict] = None,
                  fingerprint: str = None) -> Dict:
    """营收便捷入口（= route_field(REVENUE)）。"""
    return route_field(REVENUE, code, year, catalog, fingerprint)
