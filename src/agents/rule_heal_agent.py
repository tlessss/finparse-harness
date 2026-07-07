"""L2 规则自愈 agent — 选对了表但 base 规则解错 → 让 LLM 提「规则增量 delta」修好。

触发场景（见 pipeline._nongreen_llm）：非绿灯 → 选表 agent 确认表没选错(still_bad) →
  真表选对但解析器在它上面解不出过锚(多半是分隔行没识别→堆桶过计 / 金额取错列)。
  改规则(切桶标记 / 认列别名)有机会修，改代码(L3)是后一层。

闭环：
  ① 拿"选对的那张表" + base 解它的逐维对锚偏差 + 当前 dimensions/aliases 喂给 LLM；
  ② LLM 提最小 delta(只加不删)；
  ③ base+delta 合并 → 在同一张表上 forced 重解 → 过锚?
  ④ 不过锚可迭代 K 轮(回喂上一轮 delta 与偏差)；
  ⑤ 过锚才算修好；由上游走复核，pass 再 save_version 固化进池。
本 agent 只负责「提 delta + 验证过锚」，不入库、不落盘（那是上游复核后的事)。
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
_UNIT_OVERRIDES = {1, 1000, 10000, 100000000}
_MAX_ROUNDS = 2


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
    """只放行 dimensions / header_aliases / unit_ratio_override，其余丢弃。"""
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
    uo = (delta or {}).get("unit_ratio_override")
    try:
        uo = int(uo)
    except (TypeError, ValueError):
        uo = None
    if uo in _UNIT_OVERRIDES:
        out["unit_ratio_override"] = uo
    return out


def _section_view(rule: dict, rule_key: str) -> dict:
    sec = (rule.get(rule_key) or {})
    return {
        "dimensions": dict(sec.get("dimensions") or {}),
        "header_aliases": {k: list(v) for k, v in (sec.get("header_aliases") or {}).items()},
        "unit_ratio_override": sec.get("unit_ratio_override"),
    }


def _prior_block(round_i: int, prior_delta: dict, prior_diff: str) -> str:
    if round_i <= 1 or not prior_delta:
        return ""
    return (f"# 上一轮已加增量（请在此基础上继续加，勿重复）\n{_json(prior_delta)}\n"
            f"上一轮后逐维偏差：\n{prior_diff}\n")


def rule_heal(code: str, year: int, field: str, chosen: dict,
              debug: bool = False, max_rounds: int = _MAX_ROUNDS) -> Dict:
    """选对表但 base 解错 → LLM 提规则 delta(最多 K 轮) → 合并重解 → 过锚?
    outcome: fixed | not_fixable | no_change | still_bad"""
    spec = get_spec(field)
    anchor = (get_anchors(code, year) or {}).get(spec.anchor_key) if spec.anchor_key else None
    rule_file = _RULE_FILE.get(field, "revenue")
    rule_key = _RULE_KEY.get(field, "revenue_breakdown")
    base_rule = load_rule(rule_file) or {}

    base_val = _reparse_under(code, year, field, chosen, None)
    accumulated: Dict = {}
    chats: List[Dict] = []
    last_reason = last_note = ""
    last_diff = _dim_diff_text(base_val or {}, spec, anchor)

    for round_i in range(1, max(1, max_rounds) + 1):
        merged = deep_merge(base_rule, {rule_key: accumulated}) if accumulated else base_rule
        view = _section_view(merged, rule_key)
        uo = view.get("unit_ratio_override")
        variables = {
            "field": field, "field_label": _LABEL.get(field, field),
            "anchor": f"{anchor:,.0f} 元" if anchor else "无",
            "round": str(round_i), "max_rounds": str(max_rounds),
            "current_dimensions": _fmt_map(view["dimensions"]),
            "current_aliases": _fmt_aliases(view["header_aliases"]),
            "current_unit_override": str(uo) if uo else "(未设,自动检测)",
            "prior_block": _prior_block(round_i, accumulated, last_diff),
            "dim_diff": last_diff,
            "value_json": _json(_reparse_under(code, year, field, chosen, merged) if accumulated else base_val),
            "table_text": _grid_to_text(chosen.get("table") or []),
        }
        messages = build_messages("rule_heal", variables)["messages"]
        raw = chat(messages, role="judge", temperature=0, model=resolve_model("rule_heal"))
        v = _extract_json(raw) or {}
        if debug:
            chats.append({"round": round_i, "prompt": messages[-1]["content"] if messages else "", "reply": raw})

        delta = _sanitize_delta(v.get("delta") or {})
        last_reason = v.get("reason") or ""
        last_note = v.get("note") or last_reason or ""

        if v.get("fixable") is False and not delta:
            return _pack(code, year, field, False, "not_fixable", accumulated, None, None, None,
                         last_note, last_reason, chats, round_i, debug)

        if not delta:
            return _pack(code, year, field, False, "no_change", accumulated, None, None, None,
                         last_note, last_reason, chats, round_i, debug)

        accumulated = deep_merge(accumulated, delta)
        merged = deep_merge(base_rule, {rule_key: accumulated})
        value = _reparse_under(code, year, field, chosen, merged)
        sig = field_plausibility(spec, value or {}, get_anchors(code, year) or {})
        last_diff = _dim_diff_text(value or {}, spec, anchor)
        if sig.get("confidence") == "high":
            return _pack(code, year, field, True, "fixed", accumulated, merged, value, sig,
                         last_note, last_reason, chats, round_i, debug)

    return _pack(code, year, field, False, "still_bad", accumulated, None,
                 _reparse_under(code, year, field, chosen, deep_merge(base_rule, {rule_key: accumulated})),
                 field_plausibility(spec, _reparse_under(code, year, field, chosen,
                                   deep_merge(base_rule, {rule_key: accumulated})) or {},
                                  get_anchors(code, year) or {}),
                 last_note, last_reason, chats, max_rounds, debug,
                 dim_diff_after=last_diff)


def _pack(code, year, field, ok, outcome, delta, merged_rule, value, sig,
          note, reason, chats, rounds_used, debug, dim_diff_after=None) -> Dict:
    out = {"code": code, "year": year, "field": field, "ok": ok, "outcome": outcome,
           "delta": delta, "note": note, "reason": reason, "rounds_used": rounds_used,
           "fixable_claim": ok or outcome not in ("not_fixable", "no_change")}
    if merged_rule and ok:
        out["merged_rule"] = merged_rule
    if value is not None:
        out["value"] = value
    if sig is not None:
        out["sig"] = sig
    if dim_diff_after:
        out["dim_diff_after"] = dim_diff_after
    elif value is not None and not ok:
        spec = get_spec(field)
        anchor = (get_anchors(code, year) or {}).get(spec.anchor_key) if spec.anchor_key else None
        out["dim_diff_after"] = _dim_diff_text(value or {}, spec, anchor)
    if debug:
        out["chat"] = chats[-1] if len(chats) == 1 else {"rounds": chats}
    return out


def _fmt_map(d: dict) -> str:
    if not d:
        return "(空)"
    return "\n".join(f"  {k} → {v}" for k, v in d.items())


def _fmt_aliases(d: dict) -> str:
    if not d:
        return "(空)"
    lines = []
    for role, vals in d.items():
        lines.append(f"  {role}: {', '.join(vals)}")
    return "\n".join(lines)


def _json(v) -> str:
    import json
    try:
        return json.dumps(v, ensure_ascii=False, indent=1)[:1800]
    except Exception:
        return str(v)[:1800]
