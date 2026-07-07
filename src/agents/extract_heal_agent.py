"""L3 抽表自愈 — 真表选对但 pdfplumber 抽碎/漏行 → 换参重抽该页 → 过锚闸。

触发:严重缺/中度缺,且选表/L2 仍不过锚。视觉 LLM 兜底留后续 Phase。
"""

from typing import Dict, List, Optional

import json

from src.eval.field_spec import get_spec
from src.eval.anchors import get_anchors
from src.eval.table_cache import get_tables, patch_page, merge_page
from src.parsers.revenue_router import field_plausibility
from src.eval.extract_profiles import get_page_profile
from src.parsers.infra.table_scanner import rescan_page_any, list_rescan_profiles
from src.parsers.infra.table_recall import _best_col_vs_anchor


def _pdf(code, year):
    import glob
    from src.config import Config
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def _pick_best_on_page(candidates: List[Dict], page: int, anchor: float,
                       hint_caption: str = "") -> Optional[Dict]:
    """同页多张重抽表:优先锚最近,平票优先标题接近原表。"""
    on_page = [t for t in candidates if t.get("page") == page and t.get("table")]
    if not on_page:
        return None
    if not anchor:
        return on_page[0]
    scored = []
    for t in on_page:
        _, rel = _best_col_vs_anchor(t.get("table") or [], anchor)
        cap = (t.get("caption") or "")
        hint = 1 if hint_caption and hint_caption[:8] in cap else 0
        scored.append((rel, -hint, t))
    scored.sort(key=lambda x: (x[0], x[1]))
    return scored[0][2]


def _reparse_forced(code, year, field, chosen_item, tables, pdf):
    """forced 重解(避免 import pipeline 循环依赖)。"""
    from src.engine_orchestrator import FinParseAI
    from src.eval.anchors import get_anchors
    spec = get_spec(field)
    anc = (get_anchors(code, year) or {}).get(spec.anchor_key or "revenue")
    amount_col = None
    if anc:
        amount_col, _ = _best_col_vs_anchor(chosen_item.get("table") or [], anc)
    sel = {**chosen_item, "amount_col": amount_col, "via": "extract_heal"}
    try:
        parser = FinParseAI()._get_parser(field, pdf)
        return parser.parse(pdf, pre_scan=tables, code=code, year=year, forced_sel=sel).get(field)
    except TypeError:
        return None


def extract_heal(code: str, year: int, field: str, chosen: dict,
                 debug: bool = False) -> Dict:
    """对选中表所在页换 pdfplumber 策略重抽 → 替换缓存 → forced 重解 → 过锚?
    outcome: fixed | still_bad | no_page"""
    spec = get_spec(field)
    page = chosen.get("page")
    if not page:
        return {"ok": False, "outcome": "no_page", "reason": "选中表无页码"}
    pdf = _pdf(code, year)
    if not pdf:
        return {"ok": False, "outcome": "no_pdf"}
    anchor = (get_anchors(code, year) or {}).get(spec.anchor_key or "revenue")
    hint_cap = (chosen.get("caption") or "")[:80]
    tries = []

    profiles_to_try = []
    saved = get_page_profile(code, year, field, page)
    if saved:
        profiles_to_try.append({
            "name": saved.get("profile") or "saved",
            "settings": saved.get("settings") or {},
            "from": "extract_profiles",
        })
    seen_settings = {json.dumps(p.get("settings") or {}, sort_keys=True) for p in profiles_to_try}
    for prof in list_rescan_profiles():
        sk = json.dumps(prof.get("settings") or {}, sort_keys=True)
        if sk in seen_settings:
            continue
        profiles_to_try.append(prof)
        seen_settings.add(sk)

    base_tables = get_tables(code, year) or []       # 原始缓存,trial 只在内存里叠,不写盘
    for prof in profiles_to_try:
        name, settings = prof["name"], prof.get("settings") or {}
        new_items = rescan_page_any(pdf, page, settings)
        if not new_items:
            tries.append({"profile": name, "n_tables": 0})
            continue
        tables = merge_page(base_tables, page, new_items)   # 内存合并(不落盘,免污染缓存)
        pick = _pick_best_on_page(tables, page, anchor, hint_cap)
        if not pick:
            tries.append({"profile": name, "n_tables": len(new_items), "picked": False})
            continue
        value = _reparse_forced(code, year, field, pick, tables, pdf)
        sig = field_plausibility(spec, value or {}, get_anchors(code, year) or {})
        ok = sig.get("confidence") == "high"
        rec = {"profile": name, "settings": settings, "n_tables": len(new_items),
               "chosen_page": page}
        tries.append(rec)
        if ok:
            patch_page(code, year, page, new_items)          # 只有过锚(确认更好)才写回磁盘
            return {"ok": True, "outcome": "fixed", "value": value, "sig": sig,
                    "chosen_table": pick, "tables": tables, "profile": name,
                    "settings": settings, "tries": tries, "debug": debug}
    return {"ok": False, "outcome": "still_bad", "tries": tries,
            "reason": f"p{page} 换 {len(profiles_to_try)} 种抽表策略仍不过锚"}
