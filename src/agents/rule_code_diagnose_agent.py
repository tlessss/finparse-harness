"""rule_code_diagnose agent — 第二阶段规则/代码诊断（骨架版）。"""

import glob
from typing import Dict, Optional

from src.config import Config
from src.prompts.context.diagnose import gather_diagnose_context
from src.prompts.registry import build_messages


def _pdf_path(code: str, year: int) -> Optional[str]:
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def prepare_rule_code_diagnose(
    code: str,
    year: int,
    field: str = "revenue_breakdown",
    stage1: Optional[Dict] = None,
) -> Dict:
    """拼第二阶段 rule/code 诊断调试包，不发送 LLM。"""
    from src.console_service import heal_debug

    diag = heal_debug(code, year, field)
    if diag.get("error"):
        return diag

    pdf = _pdf_path(code, year)
    variables, meta = gather_diagnose_context(code, year, field, diag, pdf_path=pdf)
    stage1 = stage1 or {}
    variables.update({
        "stage1_decision": stage1.get("decision", "unknown"),
        "stage1_root_cause": stage1.get("root_cause", "unknown"),
        "stage1_next_action": stage1.get("next_action", "none"),
        "stage1_summary": stage1.get("summary", ""),
    })
    built = build_messages("rule_code_diagnose", variables)
    return {
        "code": code,
        "year": year,
        "field": field,
        "diag": diag,
        "messages": built["messages"],
        "meta": meta,
        "agent_id": built["agent_id"],
        "version": built["version"],
    }
