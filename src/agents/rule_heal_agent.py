"""L2 规则自愈 agent — 选对了表但 base 规则解错 → 让 LLM 提「规则增量 delta」修好。

触发场景（见 pipeline._nongreen_llm）：非绿灯 → 选表 agent 确认表没选错(still_bad) →
  真表选对但解析器在它上面解不出过锚(多半是分隔行没识别→堆桶过计 / 金额取错列)。
  改规则(切桶标记 / 认列别名)有机会修，改代码(L3)是后一层。

闭环：
  ① 拿"选对的那张表" + base 解它的逐维对锚偏差 + 当前 dimensions/aliases 喂给 LLM；
  ② LLM 提最小 delta(只加不删)；
  ③ base+delta 合并 → 在同一张表上 forced 重解 → 过锚?
  ④ 过锚才算修好；由上游走复核，pass 再 save_version 把这条规则固化进池([[rule-versions]])。
本 agent 只负责「提 delta + 验证过锚」，不入库、不落盘（那是上游复核后的事）。
"""

from typing import Dict, List, Optional

from src.eval.field_spec import get_spec, as_dims
from src.eval.anchors import get_anchors
from src.eval.table_cache import get_tables
from src.parsers.revenue_router import field_plausibility
from src.parsers.infra.rule_loader import load_rule, override_rule
from src.parsers.infra.rule_versions import deep_merge
from src.prompts.registry import build_messages
from src.agents.llm_client import chat
from src.agents.llm_routing import resolve_model
from src.agents.llm_judge import _extract_json, _grid_to_text

_LABEL = {"revenue_breakdown": "营业收入构成", "cost_breakdown": "营业成本构成"}
_RULE_FILE = {"revenue_breakdown": "revenue", "cost_breakdown": "cost"}
_RULE_KEY = {"revenue_breakdown": "revenue_breakdown", "cost_breakdown": "cost_breakdown"}
_ALLOWED_DIMS = {"industries", "segments", "regions", "by_channel"}
_ALLOWED_ROLES = {"name", "revenue", "ratio", "cost", "gross"}


def _pdf(code, year):
    import glob
    from src.config import Config
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def _dim_diff_text(value, spec, anchor) -> str:
    """逐维分项和 vs 锚：把"哪个桶堆多了/差多少"摊给 LLM。"""
    if not anchor:
        return "无锚，无法体检"
    parts = []
    for dim, rows in as_dims(value, spec).items():
        s = sum((r.get(spec.amount_key) or 0) for r in (rows or []))
        if not s:
            continue
        rel = (s - anchor) / anchor * 100
        parts.append(f"{dim}: 和={s:,.0f} 对锚{rel:+.1f}% n={len(rows)} {'≈锚✓' if abs(rel)<=3 else '对不上✗'}")
    return " | ".join(parts) or "各维度全空（认列/切桶全失败）"


def _reparse_under(code, year, field, chosen, rule) -> Optional[dict]:
    """在合并规则下 forced 重解那张选中表。rule=None 表示用当前 base（不覆盖）。"""
    from src.engine_orchestrator import FinParseAI
    from src.parsers.infra.table_recall import _best_col_vs_anchor
    tables, pdf = get_tables(code, year), _pdf(code, year)
    anc = (get_anchors(code, year) or {}).get("revenue")
    amount_col = None
    if anc:
        amount_col, _ = _best_col_vs_anchor(chosen.get("table") or [], anc)
    sel = {**chosen, "amount_col": amount_col, "via": "rule_heal"}
    parser = FinParseAI()._get_parser(field, pdf)

    def _run():
        try:
            return parser.parse(pdf, pre_scan=tables, code=code, year=year, forced_sel=sel).get(field)
        except TypeError:
            return None
    if rule is None:
        return _run()
    with override_rule(_RULE_FILE.get(field, "revenue"), rule):
        return _run()


def _sanitize_delta(delta: dict) -> dict:
    """只放行 dimensions(值∈四桶) 与 header_aliases(角色合法、值是字符串列表)，其余丢弃。防 LLM 乱加键。"""
    out = {}
    dims = (delta or {}).get("dimensions") or {}
    clean_dims = {str(k): v for k, v in dims.items()
                  if isinstance(k, str) and k.strip() and v in _ALLOWED_DIMS}
    if clean_dims:
        out["dimensions"] = clean_dims
    aliases = (delta or {}).get("header_aliases") or {}
    clean_al = {}
    for role, vals in aliases.items():
        if role in _ALLOWED_ROLES and isinstance(vals, list):
            strs = [str(x).strip() for x in vals if isinstance(x, str) and str(x).strip()]
            if strs:
                clean_al[role] = strs
    if clean_al:
        out["header_aliases"] = clean_al
    return out


def rule_heal(code: str, year: int, field: str, chosen: dict,
              debug: bool = False) -> Dict:
    """选对表但 base 解错 → LLM 提规则 delta → 合并重解 → 过锚?。
    返回 {ok, outcome, delta, merged_rule, value, sig, note, reason, chat?}：
      ok=True/outcome='fixed' —— delta 让这张表过锚了（上游再走复核决定入库）
      outcome='not_fixable'   —— LLM 判加规则修不了（小计混入/口径差/真缺行）
      outcome='no_change'     —— 没给出有效 delta
      outcome='still_bad'     —— 给了 delta 但合并后仍不过锚。"""
    spec = get_spec(field)
    anchor = (get_anchors(code, year) or {}).get(spec.anchor_key) if spec.anchor_key else None
    rule_file = _RULE_FILE.get(field, "revenue")
    rule_key = _RULE_KEY.get(field, "revenue_breakdown")
    base_rule = load_rule(rule_file) or {}
    section = (base_rule.get(rule_key) or {})

    base_val = _reparse_under(code, year, field, chosen, None)     # base 解这张选中表
    variables = {
        "field": field, "field_label": _LABEL.get(field, field),
        "anchor": f"{anchor:,.0f} 元" if anchor else "无",
        "current_dimensions": _fmt_map(section.get("dimensions") or {}),
        "current_aliases": _fmt_map(section.get("header_aliases") or {}),
        "dim_diff": _dim_diff_text(base_val or {}, spec, anchor),
        "value_json": _json(base_val),
        "table_text": _grid_to_text(chosen.get("table") or []),
    }
    messages = build_messages("rule_heal", variables)["messages"]
    raw = chat(messages, role="judge", temperature=0, model=resolve_model("rule_heal"))
    v = _extract_json(raw) or {}
    chat_log = {"prompt": messages[-1]["content"] if messages else "", "reply": raw} if debug else None

    delta = _sanitize_delta(v.get("delta") or {})
    reason = v.get("reason")
    note = v.get("note") or reason or ""
    base_out = {"code": code, "year": year, "field": field, "delta": delta,
                "note": note, "reason": reason, "fixable_claim": v.get("fixable")}
    if debug:
        base_out["chat"] = chat_log

    if v.get("fixable") is False and not delta:
        return {**base_out, "ok": False, "outcome": "not_fixable"}
    if not delta:
        return {**base_out, "ok": False, "outcome": "no_change"}

    merged = deep_merge(base_rule, {rule_key: delta})
    value = _reparse_under(code, year, field, chosen, merged)
    sig = field_plausibility(spec, value or {}, get_anchors(code, year) or {})
    ok = sig.get("confidence") == "high"
    return {**base_out, "ok": ok, "outcome": "fixed" if ok else "still_bad",
            "merged_rule": merged if ok else None, "value": value, "sig": sig,
            "dim_diff_after": _dim_diff_text(value or {}, spec, anchor)}


def _fmt_map(d: dict) -> str:
    if not d:
        return "(空)"
    return "\n".join(f"  {k} → {v}" for k, v in d.items())


def _json(v) -> str:
    import json
    try:
        return json.dumps(v, ensure_ascii=False, indent=1)[:1800]
    except Exception:
        return str(v)[:1800]
