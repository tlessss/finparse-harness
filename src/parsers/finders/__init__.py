"""抽表器层入口 —— 按 fingerprint 路由到专用抽表器，否则用默认。

和解析器路由同构：命中某报告 fingerprint + 字段的认证专用抽表器 → 用它；否则默认启发式。
专用抽表器登记在 goldset/finders_catalog.json（认证时写入）。
"""

import importlib.util
import json
import os

_CATALOG = "goldset/finders_catalog.json"


def _catalog():
    if os.path.exists(_CATALOG):
        try:
            return json.load(open(_CATALOG, encoding="utf-8")).get("finders", [])
        except Exception:
            return []
    return []


def _load_find_fn(path):
    spec = importlib.util.spec_from_file_location("_finder_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find


def find_tables(tables, field, code=None, year=None):
    """路由：命中 fingerprint+field 的专用抽表器用它，否则默认。返回候选表(降序)。"""
    if code and year:
        try:
            from src.eval.route_index import fingerprint_of
            fp = fingerprint_of(code, year)
        except Exception:
            fp = None
        if fp:
            for f in _catalog():
                if f.get("field") == field and fp in (f.get("fingerprints") or []):
                    try:
                        return _load_find_fn(f["path"])(tables, field) or []
                    except Exception:
                        break          # 专用器坏了 → 退回默认，不让它 crash 整条路
    from src.parsers.finders import default
    return default.find(tables, field)
