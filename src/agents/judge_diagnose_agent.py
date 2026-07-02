"""judge_diagnose agent — 分层诊断（误报/选表/跨页/数据层）。"""

import glob
from typing import Dict, Optional

from src.config import Config
from src.eval.table_cache import get_tables
from src.prompts.context.parse import (
    anchor_summary_text,
    dims_summary_text,
    missing_dims,
)
from src.prompts.context.pipeline import (
    cross_page_suspect,
    field_sig,
    pick_meta_text,
    select_pick,
)
from src.prompts.context.table import (
    candidate_table_lines,
    next_table_content,
    table_preview,
)


def _pdf_path(code: str, year: int) -> Optional[str]:
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def _dims_agree_text(v) -> str:
    if v is True:
        return "是"
    if v is False:
        return "否"
    return "未知"


def _anchor_diff_text(dims, anchor) -> str:
    """逐维列出对锚的带符号偏差 + 过锚/未过锚。别再用 min 掩盖崩掉的维度。"""
    if not anchor:
        return "无锚"
    if not dims:
        return "无可比较维度"
    parts = []
    for d in dims:
        s = float(d.get("sum") or 0)
        rel = (s - float(anchor)) / float(anchor) * 100
        parts.append(f"{d.get('dim')} {rel:+.1f}%({'过锚' if d.get('match') else '未过锚'})")
    return " | ".join(parts)


def _completeness_text(dims, missing) -> str:
    """维度完整性确定性信号：任一维度缺失 或 分项和未过锚(疑似缺行) → 不完整（=真 bug，非口径差）。"""
    short = [d.get("dim") for d in (dims or []) if not d.get("match")]
    if not missing and not short:
        return "完整（所有预期维度均过锚）"
    parts = []
    if missing:
        parts.append(f"缺失维度: {missing}")
    if short:
        parts.append(f"分项和未过锚(疑似缺行): {short}")
    return "不完整 — " + "；".join(parts)


def prepare_judge_diagnose(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """拼 judge_diagnose 调试包 messages，不发送 LLM。"""
    from src.agents.llm_judge import build_judge_messages
    from src.console_service import heal_debug
    from src.engine_orchestrator import FinParseAI

    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    tables = get_tables(code, year)
    if tables is None:
        return {"error": "无缓存（先解析一次该报告）"}

    parser = FinParseAI()._get_parser(field, pdf)
    try:
        out = parser.parse(pdf, pre_scan=tables, code=code, year=year)
    except Exception as e:
        return {"error": "解析异常: " + str(e)[:100]}
    value = out.get(field)
    prov = out.get("溯源") or {}
    if isinstance(prov.get(field), dict):
        prov = prov[field]

    diag = heal_debug(code, year, field)
    if diag.get("error"):
        return diag

    sig = field_sig(field)
    pick = select_pick(tables, code, year, field)
    pick_table = (pick or {}).get("table") or []
    next_lines = next_table_content(tables, pick)          # 只给紧接的下一张表内容（去噪）
    candidate_lines = candidate_table_lines(tables, code, year, sig)
    cross_suspect, _ = cross_page_suspect(pick, next_lines)
    missing = missing_dims(value)
    unit_label = ""
    try:
        from src.console_service import _field_unit_label  # reuse existing helper

        unit_label = _field_unit_label(code, year, field) or ""
    except Exception:
        pass
    unit_note = (
        f"【单位提示】源文金额单位为「{unit_label}」；解析结果已换算为「元」，请先换算后再判断。"
        if unit_label
        else ""
    )
    extra_vars = {
        "pick_meta": pick_meta_text(pick),
        "anchor_summary": anchor_summary_text(diag.get("anchor")),
        "dims_summary": dims_summary_text(diag.get("dims") or [], diag.get("anchor")),
        "missing_dims_text": str(missing) if missing else "无",
        "dims_agree_text": _dims_agree_text(diag.get("dims_agree")),
        "anchor_diff_text": _anchor_diff_text(diag.get("dims") or [], diag.get("anchor")),
        "completeness_text": _completeness_text(diag.get("dims") or [], missing),
        "table_preview": table_preview(pick_table),
        "neighbor_tables": "\n\n".join(next_lines) if next_lines else "(无)",
        "candidates": "\n".join(candidate_lines) if candidate_lines else "(无)",
        "unit_note": unit_note,
    }
    messages, grounding = build_judge_messages(
        field,
        code,
        year,
        value,
        provenance=prov,
        unit_label=unit_label,
        agent_id="judge_diagnose",
        extra_vars=extra_vars,
    )
    if messages is None:
        return {"error": "无源文(溯源+RAG都没有),无法对话", "grounding": grounding}
    return {
        "code": code,
        "year": year,
        "field": field,
        "grounding": grounding,
        "unit": unit_label,
        "messages": messages,
        "result": value,
        "meta": {
            "pick_page": (pick or {}).get("page"),
            "pick_via": (pick or {}).get("via"),
            "cross_page_suspect": cross_suspect,
            "missing_dims": missing,
            "candidate_count": len(candidate_lines),
            "neighbor_count": len(next_lines),
            "need_heal": diag.get("need_heal"),
            "verdict": diag.get("verdict"),
            "reason": diag.get("reason"),
        },
        "agent_id": "judge_diagnose",
        "version": "v1",
    }
