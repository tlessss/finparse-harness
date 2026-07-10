"""超级工作台后端 —— 给前端喂三样东西：
  ① 某公司的**分层诊断档案**(dossier:抽表/选表/解析/路由/L3探针的确定性事实)+ 本轮结局;
  ② **管家诊断**(steward_diagnose:强模型分层归因 → 根因/为什么没自愈/处方);
  ③ **批量路线图**(steward_review:最大根因桶 + 自驱路线图)。
前两个按公司,第三个是全局治理视图。
"""

import json
import os
from typing import Dict

_REVIEW_PATH = "goldset/steward_review.json"
_PROGRESS = "goldset/pipeline_progress.json"
_DIAG_STORE = "goldset/steward_diagnoses.json"   # 管家诊断存档(按 code_year_field)


def _load_diags() -> Dict:
    if os.path.exists(_DIAG_STORE):
        try:
            return json.load(open(_DIAG_STORE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_diag(key: str, result: Dict) -> None:
    diags = _load_diags()
    diags[key] = result
    try:
        os.makedirs("goldset", exist_ok=True)
        json.dump(diags, open(_DIAG_STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    except Exception:
        pass


def saved_diagnosis(code: str, year: int = 2025, field: str = "revenue_breakdown") -> Dict:
    """读某公司的管家诊断存档(无 LLM,秒回;没存过返回 {})——开页先显示历史诊断。"""
    return _load_diags().get(f"{code}_{year}_{field}") or {}


def _outcome_of(code: str) -> str:
    try:
        prog = json.load(open(_PROGRESS, encoding="utf-8"))
        for d in prog.get("done", []):
            if d.get("code") == code:
                return d.get("outcome")
    except Exception:
        pass
    return None


def workbench(code: str, year: int = 2025, field: str = "revenue_breakdown") -> Dict:
    """确定性:分层证据档案 + 本轮结局(不发 LLM,秒回)。"""
    from src.agents.steward_probes import collect_dossier
    return {"code": code, "year": year, "field": field,
            "dossier": collect_dossier(code, year, field), "outcome": _outcome_of(code)}


def diagnose(code: str, year: int = 2025, field: str = "revenue_breakdown") -> Dict:
    """管家单案:① 深诊断(根因/处方)② 若过锚,顺带跑管家裁决(强模型判真/假 hold)。
    两样都回给前端展示——人据此决定是否人工介入。"""
    from src.agents.steward_agent import steward_diagnose, steward_adjudicate
    d = steward_diagnose(code, year, field)
    d.pop("dossier", None)
    try:                                              # 过锚(可能是"过锚但复核hold")→ 加二次裁决
        from src.parsers.revenue_router import field_plausibility
        from src.eval.field_spec import get_spec
        from src.eval.anchors import get_anchors
        from src.engine_orchestrator import FinParseAI
        from src.parsers.infra.pdf_locator import ensure_pdf
        from src.eval.table_cache import get_tables
        spec = get_spec(field)
        pdf = ensure_pdf(code, year)
        tables = get_tables(code, year)
        val = (FinParseAI()._get_parser(field, pdf).parse(pdf, pre_scan=tables, code=code, year=year) or {}).get(field)
        sig = field_plausibility(spec, val, get_anchors(code, year) or {})
        if sig.get("confidence") == "high" and val:
            d["adjudication"] = steward_adjudicate(code, year, field, val, sig, spec)
        else:
            d["adjudication"] = {"decision": "n/a", "reason": "未过金额锚，管家裁决只处理'过锚但复核hold'"}
    except Exception as e:
        d["adjudication"] = {"decision": "error", "reason": str(e)[:80]}
    import time
    d["diagnosed_at"] = time.strftime("%Y-%m-%d %H:%M")
    _save_diag(f"{code}_{year}_{field}", d)          # 记录存档:免重跑 + 人可回看
    return d


def roadmap() -> Dict:
    """最近一次批量复盘的路线图(治理视图)。读持久化的 steward_review.json。"""
    if os.path.exists(_REVIEW_PATH):
        try:
            return json.load(open(_REVIEW_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"n_failures": 0, "buckets": {}, "diagnoses": [], "roadmap": {}}
