"""L3 抽表配置经验库 — 读回固化 profile,在 get_tables 时补丁缓存,免再走 heal LLM 链。

两层索引:
  1. 报告级 goldset/extract_profiles/{code}_{year}.json  (本报告 L3 成功落盘)
  2. 版式级 goldset/extract_profiles/fp_index.json       (同 fingerprint 跨报告复用)
"""

import json
import os
from typing import Dict, List, Optional, Tuple

_PROFILE_DIR = "goldset/extract_profiles"
_FP_INDEX = os.path.join(_PROFILE_DIR, "fp_index.json")


def _report_path(code: str, year: int) -> str:
    return os.path.join(_PROFILE_DIR, f"{code}_{year}.json")


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _fp_key(field: str, fp: str, page: int) -> str:
    return f"{field}|{fp}|{page}"


def get_report_profiles(code: str, year: int) -> Dict[str, dict]:
    """page(str) → {profile, settings, field, note, cache_synced?}"""
    return _load_json(_report_path(code, year))


def get_page_profile(code: str, year: int, field: str, page: int) -> Optional[dict]:
    """合并报告级 + 版式级 profile,报告级优先。"""
    rp = get_report_profiles(code, year).get(str(page))
    if rp and (not rp.get("field") or rp.get("field") == field):
        return rp
    try:
        from src.eval.route_index import fingerprint_of
        fp = fingerprint_of(code, year)
    except Exception:
        fp = None
    if not fp:
        return None
    ent = _load_json(_FP_INDEX).get(_fp_key(field, fp, page))
    return ent


def mark_report_profile_synced(code: str, year: int, page: int) -> None:
    """L3 已 patch 缓存后标记,避免 get_tables 重复重扫。"""
    path = _report_path(code, year)
    doc = _load_json(path)
    if str(page) not in doc:
        return
    doc[str(page)]["cache_synced"] = True
    _save_json(path, doc)


def save_report_profile(code: str, year: int, page: int, profile_name: str,
                        settings: dict, field: str = "revenue_breakdown",
                        note: str = "") -> str:
    """报告级落盘(L3 consolidate 调用);cache_synced=False 待 get_tables 补丁。"""
    path = _report_path(code, year)
    doc = _load_json(path)
    doc[str(page)] = {
        "profile": profile_name,
        "settings": settings or {},
        "field": field,
        "note": note,
        "cache_synced": False,
    }
    _save_json(path, doc)
    return path


def save_fingerprint_profile(code: str, year: int, page: int, profile_name: str,
                             settings: dict, field: str = "revenue_breakdown",
                             note: str = "") -> None:
    """版式级索引,供同模板其它报告复用。"""
    try:
        from src.eval.route_index import fingerprint_of
        fp = fingerprint_of(code, year)
    except Exception:
        return
    if not fp:
        return
    idx = _load_json(_FP_INDEX)
    k = _fp_key(field, fp, page)
    ent = idx.get(k) or {}
    ent.update({
        "profile": profile_name,
        "settings": settings or {},
        "field": field,
        "note": note,
        "origin": f"{code}_{year}",
        "synced_reports": ent.get("synced_reports") or [],
    })
    idx[k] = ent
    _save_json(_FP_INDEX, idx)


def _pages_needing_apply(code: str, year: int, field: str) -> List[Tuple[int, dict, str]]:
    """返回 [(page, entry, source)] 待补丁页。source ∈ report | fingerprint。"""
    out: List[Tuple[int, dict, str]] = []
    seen = set()

    for ps, ent in get_report_profiles(code, year).items():
        if ent.get("cache_synced"):
            continue
        if ent.get("field") and ent.get("field") != field:
            continue
        try:
            pg = int(ps)
        except (TypeError, ValueError):
            continue
        out.append((pg, ent, "report"))
        seen.add(pg)

    try:
        from src.eval.route_index import fingerprint_of
        fp = fingerprint_of(code, year)
    except Exception:
        fp = None
    if fp:
        rk = f"{field}|{fp}|"
        for k, ent in _load_json(_FP_INDEX).items():
            if not k.startswith(rk):
                continue
            try:
                pg = int(k.split("|")[-1])
            except (TypeError, ValueError):
                continue
            if pg in seen:
                continue
            if f"{code}_{year}" in (ent.get("synced_reports") or []):
                continue
            out.append((pg, ent, "fingerprint"))
            seen.add(pg)
    return out


def _mark_synced(code: str, year: int, page: int, field: str, source: str) -> None:
    rp = _report_path(code, year)
    doc = _load_json(rp)
    if str(page) in doc:
        doc[str(page)]["cache_synced"] = True
        _save_json(rp, doc)
    if source != "fingerprint":
        return
    try:
        from src.eval.route_index import fingerprint_of
        fp = fingerprint_of(code, year)
    except Exception:
        return
    if not fp:
        return
    idx = _load_json(_FP_INDEX)
    k = _fp_key(field, fp, page)
    if k not in idx:
        return
    synced = set(idx[k].get("synced_reports") or [])
    synced.add(f"{code}_{year}")
    idx[k]["synced_reports"] = sorted(synced)
    _save_json(_FP_INDEX, idx)


def apply_saved_extract_profiles(code: str, year: int, tables: list,
                                 pdf: Optional[str] = None,
                                 field: str = "revenue_breakdown") -> dict:
    """把经验库中的 pdfplumber 配置应用到 tables(内存);返回 {tables, patched, applied}。"""
    pending = _pages_needing_apply(code, year, field)
    if not pending:
        return {"tables": tables, "patched": False, "applied": []}
    if not pdf:
        from src.eval.table_cache import _pdf_path
        pdf = _pdf_path(code, year)
    if not pdf:
        return {"tables": tables, "patched": False, "applied": []}

    from src.parsers.infra.table_scanner import rescan_page_any
    merged = list(tables or [])
    applied = []
    for page, ent, source in pending:
        settings = ent.get("settings") or {}
        new_items = rescan_page_any(pdf, page, settings)
        if not new_items:
            continue
        merged = [t for t in merged if t.get("page") != page] + new_items
        _mark_synced(code, year, page, field, source)
        applied.append({
            "page": page, "profile": ent.get("profile"), "source": source,
            "settings": settings, "n_tables": len(new_items),
        })
    return {"tables": merged, "patched": bool(applied), "applied": applied}
