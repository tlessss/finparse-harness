"""选表 agent — 把一份年报的全部表(摘要)发给 LLM，让它认出唯一的目标构成表。

触发场景：确定性选表拿不准 / 复核 agent 判 wrong_table → 派它直接重选。
先做"全表摘要发给 LLM 认表"的最朴素版，验证 LLM 认不认得出。
"""

from typing import Dict, Optional

from src.eval.table_cache import get_tables
from src.prompts.registry import build_messages
from src.agents.llm_client import chat
from src.agents.llm_routing import resolve_model
from src.agents.llm_judge import _extract_json

_LABEL = {"revenue_breakdown": "营业收入构成", "cost_breakdown": "营业成本构成",
          "rnd_info": "研发费用明细", "employees": "员工构成",
          "top_clients": "前五大客户", "top_suppliers": "前五大供应商"}
_ANCHOR_KEY = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd_expense"}
_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd",
        "employees": "employee", "top_clients": "client", "top_suppliers": "supplier"}
TOP_K = 20


def _digest(cands, anchor=None, all_tables=None) -> str:
    """候选摘要：标题 +「整表去数字的文字骨架」(维度标签全露出来,不靠标题) + 确定性"有列合计≈锚"标注
    + 跨页续表标注(物理紧邻的下一页纯数字续表)。
    去数字是因为数字是噪音、还常被抽碎;维度标签(业务类型/分地区/集成电路…)才是认表的信号。"""
    from src.parsers.infra.table_recall import _table_textdoc, _best_col_vs_anchor, following_tables
    lines = []
    for i, t in enumerate(cands):
        g = t.get("table") or []
        nr = len(g)
        nc = max((len(r) for r in g), default=0)
        cap = (t.get("caption") or "").replace("\n", " ").strip()[:110] or "(无标题)"
        skel = _table_textdoc(g, "")                         # 去数字的全表文字(去重保序)
        hint = ""
        if anchor:
            try:
                _, rel = _best_col_vs_anchor(g, anchor)
                if rel is not None and rel < 0.05:
                    hint = f" ✔有列合计≈营收锚(差{rel * 100:.1f}%)"
            except Exception:
                pass
        cont = ""                                            # 跨页续表标注
        if all_tables:
            try:
                fol = following_tables(all_tables, t)
                if fol:
                    ps = "/".join(f"p{x.get('page')}({len(x.get('table') or [])}行)" for x in fol)
                    cont = f" ⟳可能跨页:紧接下方/下一页有续表 {ps}(选它系统会自动拼上再判锚)"
            except Exception:
                pass
        lines.append(f"#{i} p{t.get('page')} {nr}x{nc}{hint}{cont} 标题「{cap}」\n   内容:{skel}")
    return "\n".join(lines)


def _conf(v) -> Optional[float]:
    try:
        return float(v.get("confidence"))
    except Exception:
        return None


def _ask(code, year, field, cands, tables, anc, stage, debug) -> Dict:
    """给一组候选表跑一次 LLM 选表。"""
    variables = {
        "field": field, "field_label": _LABEL.get(field, field),
        "anchor": f"{anc:,.0f} 元" if anc else "无",
        "table_digest": _digest(cands, anc, all_tables=tables),
    }
    messages = build_messages("select_table", variables)["messages"]
    raw = chat(messages, role="judge", temperature=0, model=resolve_model("select_table"))
    v = _extract_json(raw) or {}
    idx = v.get("chosen")
    chosen = cands[idx] if isinstance(idx, int) and 0 <= idx < len(cands) else None
    out = {"code": code, "year": year, "field": field, "stage": stage,
           "chosen_index": idx, "chosen_page": (chosen or {}).get("page"),
           "chosen_caption": (chosen or {}).get("caption"), "chosen_table": chosen,
           "caliber_gap": v.get("caliber_gap"), "confidence": v.get("confidence"),
           "reason": v.get("reason"), "n_tables": len(tables), "n_candidates": len(cands),
           "_v": v}
    if debug:
        out["_prompt"] = messages[1]["content"]
        out["_raw"] = raw
    return out


def select_table_llm(code: str, year: int, field: str = "revenue_breakdown",
                     debug: bool = False, conf_min: float = 0.6) -> Dict:
    """向量召回 top-20 候选(带标题)发 LLM 认表；认不出(chosen=-1/置信低)则回退发全部表再判一次。"""
    tables = get_tables(code, year)
    if not tables:
        return {"error": "无缓存表"}
    from src.eval.anchors import get_anchors
    from src.parsers.infra.table_recall import vector_recall
    anc = (get_anchors(code, year) or {}).get(_ANCHOR_KEY.get(field, "revenue"))
    cands = vector_recall(tables, _SIG.get(field, "revenue"), top_k=TOP_K, threshold=0.0)[:TOP_K]

    out = _ask(code, year, field, cands, tables, anc, "top20", debug)
    c = _conf(out["_v"])
    need_fallback = (out["chosen_page"] is None) or (out["_v"].get("chosen") == -1) or (c is not None and c < conf_min)
    if need_fallback and len(tables) > len(cands):                # top20 没认出 → 全量再判
        fb = _ask(code, year, field, tables, tables, anc, "fallback_all", debug)
        fb["fallback_from"] = {"chosen_page": out["chosen_page"], "confidence": out["confidence"]}
        fb.pop("_v", None)
        return fb
    out.pop("_v", None)
    return out
