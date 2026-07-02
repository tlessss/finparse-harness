"""诊断 agent Context 组装 — 聚合 table/parse/pipeline/code 各 Pack。"""

from typing import Any, Dict, Optional, Tuple

from src.eval.table_cache import get_tables
from src.prompts.context.code import load_revenue_yaml, parser_source_snippets
from src.prompts.context.parse import (
    anchor_summary_text,
    dims_summary_text,
    missing_dims,
    parse_field_value,
    parse_value_json,
)
from src.prompts.context.pipeline import cross_page_suspect, field_sig, pick_meta_text, select_pick
from src.prompts.context.table import candidate_table_lines, neighbor_table_lines, table_preview


def gather_diagnose_context(
    code: str,
    year: int,
    field: str,
    diag: Dict[str, Any],
    pdf_path: Optional[str] = None,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """收集 diagnose 模板变量 + 结构化 meta（供前端展示）。"""
    tables = get_tables(code, year) or []
    sig = field_sig(field)
    pick = select_pick(tables, code, year, field)
    table = (pick or {}).get("table") or []

    neighbor_lines = neighbor_table_lines(tables, (pick or {}).get("page"))
    cand_lines = candidate_table_lines(tables, code, year, sig)
    value = parse_field_value(code, year, field, tables, pdf_path)

    missing = missing_dims(value)
    cross_suspect, cross_hint = cross_page_suspect(pick, neighbor_lines)

    parser_code = ""
    if pdf_path:
        try:
            from src.engine_orchestrator import FinParseAI
            parser = FinParseAI()._get_parser(field, pdf_path)
            parser_code = parser_source_snippets(parser)
        except Exception:
            pass

    variables = {
        "field": field,
        "verdict": diag.get("verdict") or "",
        "reason": diag.get("reason") or "",
        "dims_summary": dims_summary_text(diag.get("dims") or [], diag.get("anchor")),
        "anchor_summary": anchor_summary_text(diag.get("anchor")),
        "pick_meta": pick_meta_text(pick),
        "missing_dims_text": str(missing) if missing else "无",
        "cross_page_hint": cross_hint,
        "table_preview": table_preview(table),
        "neighbor_tables": "\n".join(neighbor_lines) if neighbor_lines else "(无)",
        "candidates": "\n".join(cand_lines) if cand_lines else "(无)",
        "parse_value": parse_value_json(value),
        "config_yaml": load_revenue_yaml(),
        "parser_code": parser_code,
    }

    meta = {
        "pick_page": (pick or {}).get("page"),
        "pick_via": (pick or {}).get("via"),
        "missing_dims": missing,
        "cross_page_suspect": cross_suspect,
        "neighbor_count": len(neighbor_lines),
        "candidate_count": len(cand_lines),
        "need_heal": diag.get("need_heal"),
    }
    return variables, meta
