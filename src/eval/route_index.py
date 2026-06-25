"""
指纹缓存 + 路由缓存 — 让"选解析器"在解析器多了之后也快

· fingerprint_of(code,year): 算版式指纹(只取文本,快)，缓存。
· 路由缓存 route_get/set/invalidate: 记住"某指纹 → 选中哪个解析器"，
  同指纹的下一份直接命中、连候选都不用跑。漂移(硬规则不过)时失效重选。
"""

import glob
import json
import os
from typing import Optional

from src.config import Config

_FP_CACHE = "goldset/fingerprint_cache.json"
_ROUTE_CACHE = "goldset/route_cache.json"


def _load(p) -> dict:
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def _save(p, d) -> None:
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def fingerprint_of(code: str, year: int) -> Optional[str]:
    """该报告的版式指纹 hash（缓存）。"""
    cache = _load(_FP_CACHE)
    k = f"{code}_{year}"
    if k in cache:
        return cache[k]
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    if not hits:
        return None
    from src.parsers.infra.layout_fingerprint import compute_fingerprint
    fp = compute_fingerprint(hits[0]).get("hash")
    cache[k] = fp
    _save(_FP_CACHE, cache)
    return fp


def _k(field: str, fp: str) -> str:
    return f"{field}|{fp}"


def route_get(field: str, fp: str) -> Optional[str]:
    return _load(_ROUTE_CACHE).get(_k(field, fp))


def route_set(field: str, fp: str, path: str) -> None:
    d = _load(_ROUTE_CACHE)
    d[_k(field, fp)] = path
    _save(_ROUTE_CACHE, d)


def route_invalidate(field: str, fp: str) -> None:
    d = _load(_ROUTE_CACHE)
    if d.pop(_k(field, fp), None) is not None:
        _save(_ROUTE_CACHE, d)
