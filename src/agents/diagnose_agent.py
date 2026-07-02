"""诊断 agent — 根因定位 + 最小修复建议（原 heal_prepare 工程化入口）。"""

import glob
from typing import Dict, Optional

from src.config import Config
from src.prompts.context.diagnose import gather_diagnose_context
from src.prompts.registry import build_messages


def _pdf_path(code: str, year: int) -> Optional[str]:
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def prepare_diagnose(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """拼诊断调试包 messages，不发送 LLM。返回 {code, year, field, diag, messages, meta, agent_id, version}。"""
    from src.console_service import heal_debug

    diag = heal_debug(code, year, field)
    if diag.get("error"):
        return diag

    pdf = _pdf_path(code, year)
    variables, meta = gather_diagnose_context(code, year, field, diag, pdf_path=pdf)
    built = build_messages("diagnose", variables)

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
