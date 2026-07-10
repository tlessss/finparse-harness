"""端到端流水线编排（单报告粒度）+ 批量成功率分析。

一份报告、一个字段的真实链路：
  冷启动解析(选中表→结构化) → 跨表锚判 field_plausibility
    绿灯(过锚, confidence=high) →[use_llm: verify_field pass]→ 入库(_auto_commit)
    非绿灯                      →[use_llm: judge_diagnose]→ 人工/二阶段(rule_code)
成功率 = 有锚字段里"绿灯(锚确认)"的比例 —— 即自主解析、无需人工的比例。

用法：
  from src.pipeline import run_report, analyze_batch
  analyze_batch(["300009","000333",...], 2025)      # 确定性成功率(不发 LLM)
"""

import glob
from collections import Counter
from typing import Dict, List, Optional

from src.config import Config
from src.eval.field_spec import get_spec
from src.eval.table_cache import get_tables
from src.parsers.revenue_router import field_plausibility

FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info",
          "employees", "top_clients", "top_suppliers"]

# 字段 → 其 base 规则文件名(load_rule 的键)。只有有规则文件的字段才走版本池。
_RULE_FILE = {"revenue_breakdown": "revenue", "cost_breakdown": "cost"}
# 字段 → 规则 YAML 里的顶层 section 键(delta 挂在它下面)。
_RULE_KEY = {"revenue_breakdown": "revenue_breakdown", "cost_breakdown": "cost_breakdown"}
_FIELD_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd",
              "employees": "employee", "top_clients": "client", "top_suppliers": "supplier"}


def _pdf(code: str, year: int, download: bool = True) -> Optional[str]:
    """PDF 路径：缓存优先，未命中且 download=True 时走巨潮按需下载。"""
    from src.parsers.infra.pdf_locator import ensure_pdf
    return ensure_pdf(code, year, download=download)


def _ensure_report_input(code: str, year: int) -> Dict:
    """跑流水线前补齐输入：PDF(缺则下载) + 抽表(空缓存当失效重扫)。
    返回 {tables, pdf, reason?, downloaded?}。"""
    from src.parsers.infra.pdf_locator import ensure_pdf, find_cached
    from src.eval.table_cache import get_tables
    had_pdf = bool(find_cached(code, year))
    pdf = ensure_pdf(code, year, download=True)
    if not pdf:
        return {"tables": None, "pdf": None, "reason": "无PDF(缓存未命中且下载失败)"}
    tables = get_tables(code, year)
    if tables is not None and len(tables) == 0:
        tables = get_tables(code, year, refresh=True)
    if tables is None:
        return {"tables": None, "pdf": pdf, "reason": "抽表失败"}
    if len(tables) == 0:
        return {"tables": [], "pdf": pdf, "reason": "PDF已就位但抽表为空"}
    out: Dict = {"tables": tables, "pdf": pdf}
    if pdf and not had_pdf:
        out["downloaded"] = True
    return out


def _cold_parse(code: str, year: int, field: str, tables, pdf: str):
    """冷启动引擎解析器解该字段（生产失败时的兜底路径，也是选表解耦后的主路径）。"""
    try:
        from src.engine_orchestrator import FinParseAI
        parser = FinParseAI()._get_parser(field, pdf)
        return parser.parse(pdf, pre_scan=tables, code=code, year=year).get(field)
    except Exception:
        return None


def _cold_parse_full(code: str, year: int, field: str, tables, pdf: str):
    """同上，但连溯源一起拿：返回 (value, provenance)。provenance = {值路径: {page, bbox}}。"""
    try:
        from src.engine_orchestrator import FinParseAI
        out = FinParseAI()._get_parser(field, pdf).parse(pdf, pre_scan=tables, code=code, year=year)
        prov = out.get("溯源") or {}
        if isinstance(prov.get(field), dict):
            prov = prov[field]
        return out.get(field), (prov if isinstance(prov, dict) else {})
    except Exception:
        return None, {}


def _emit_event_safe(run_id: str, code: str, year: int, field: str, agent_id: str,
                     event_type: str, outcome: str = None, payload: Dict = None) -> None:
    """写流程事件（观测失败不影响主链）。"""
    if not run_id:
        return
    try:
        from src.eval.test_store import emit_event
        emit_event(run_id, code, year, field, agent_id, event_type, outcome=outcome, payload=payload or {})
    except Exception:
        pass


def _value_stats(value: Dict) -> Dict:
    """把解析值压成轻量统计，便于时间线展示输入输出。"""
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    out: Dict = {"dims": list(value.keys()), "rows": {}}
    for k, v in value.items():
        out["rows"][k] = len(v) if isinstance(v, list) else 0
    return out


def _emit_llm_events(run_id: str, code: str, year: int, field: str, rec: Dict) -> None:
    """把一次 LLM 运行结果拆为可观测事件。"""
    if not rec:
        return
    llm_kind = rec.get("llm_kind")
    if llm_kind == "verify":
        _emit_event_safe(run_id, code, year, field, "verify", "verify",
                         outcome=rec.get("verdict"),
                         payload={"summary": rec.get("summary"),
                                  "input": {"value_stats": _value_stats(rec.get("value"))},
                                  "output": {"verdict": rec.get("verdict"), "suspects": rec.get("suspects")}})
    elif llm_kind == "diagnose":
        _emit_event_safe(run_id, code, year, field, "judge_diagnose", "diagnose",
                         outcome=rec.get("decision"),
                         payload={"summary": rec.get("summary"),
                                  "input": {"outcome": rec.get("outcome"), "routed_cat": rec.get("routed_cat")},
                                  "output": {"decision": rec.get("decision"),
                                             "root_cause": rec.get("root_cause"),
                                             "next_action": rec.get("next_action")}})

    heal = rec.get("heal") or {}
    if heal:
        _emit_event_safe(run_id, code, year, field, "select_table", "heal_select",
                         outcome=heal.get("outcome"),
                         payload={"summary": heal.get("select_reason"),
                                  "input": {"outcome_before": rec.get("outcome")},
                                  "output": {"chosen_page": heal.get("chosen_page"),
                                             "chosen_caption": heal.get("chosen_caption"),
                                             "select_stage": heal.get("select_stage"),
                                             "caliber_gap": heal.get("caliber_gap")}})
    if rec.get("rule_heal"):
        _emit_event_safe(run_id, code, year, field, "rule_heal", "rule_heal",
                         outcome=(rec.get("rule_heal") or {}).get("outcome"),
                         payload={"summary": (rec.get("rule_heal") or {}).get("reason"),
                                  "output": rec.get("rule_heal")})
    if rec.get("extract_heal"):
        _emit_event_safe(run_id, code, year, field, "extract_heal", "extract_heal",
                         outcome=(rec.get("extract_heal") or {}).get("outcome"),
                         payload={"summary": (rec.get("extract_heal") or {}).get("reason"),
                                  "output": rec.get("extract_heal")})
    if rec.get("codegen"):
        _emit_event_safe(run_id, code, year, field, "codegen", "codegen",
                         outcome=(rec.get("codegen") or {}).get("outcome"),
                         payload={"output": rec.get("codegen")})


def _parse_versioned(code, year, field, tables, pdf, anchors, spec) -> Dict:
    """base 优先 → 不过锚则扫规则版本池，取第一个过锚的版本。
    返回 {value, sig, version, sweep}：
      version='base'      —— base 就过锚，或池里也没有过锚的（原样退回 base 结果）
      version=<版本 id>   —— 该版本救活了(过锚)
      sweep=[{id, ok, note}, ...] —— 试过哪些版本、各自过没过（前端/DB 可看）。
    base 优先 = 结构上不回归：base 过锚的报告直接返回，不动版本池。"""
    base_val = _cold_parse(code, year, field, tables, pdf)
    base_sig = field_plausibility(spec, base_val, anchors or {}) if base_val else {}
    # base 已过锚 / 无锚字段 → 直接收工（无锚判不了，不折腾）
    if not spec.anchor_key or base_sig.get("confidence") == "high":
        return {"value": base_val, "sig": base_sig, "version": "base", "sweep": []}

    # 不过锚（或没解出）且有锚 → ① 扫规则版本池 ② 跨页拼接兜底（都不发 LLM）
    sweep = []
    rule_file = _RULE_FILE.get(field)
    if rule_file:
        from src.parsers.infra.rule_versions import load_versions, merged_rule
        from src.parsers.infra.rule_loader import override_rule, load_rule
        for ver in load_versions(rule_file):
            merged = merged_rule(load_rule(rule_file), ver)
            with override_rule(rule_file, merged):
                val = _cold_parse(code, year, field, tables, pdf)
            sig = field_plausibility(spec, val, anchors or {}) if val else {}
            ok = sig.get("confidence") == "high"
            sweep.append({"id": ver["id"], "ok": ok, "note": ver.get("note")})
            if ok:                                                # 第一个过锚的版本 = 赢家
                return {"value": val, "sig": sig, "version": ver["id"], "sweep": sweep}

    # 跨页拼接兜底：只对有规则文件的分项字段(营收/成本)试，其它字段(研发/员工/前五大)行为与改动前完全一致。
    if rule_file:
        st = _stitch_cold(code, year, field, tables, pdf, anchors, spec)  # 跨页续表拼接(锚闸)
        if st:
            st["sweep"] = sweep
            return st
    return {"value": base_val, "sig": base_sig, "version": "base", "sweep": sweep}


def _stitch_cold(code, year, field, tables, pdf, anchors, spec) -> Optional[Dict]:
    """冷启动跨页兜底:确定性选中表若因跨页被截断而不过锚 → 拼物理紧邻的下一页续表再判锚。
    过锚才返回(version='base+跨页拼接'),否则 None——锚闸兜底,和选表自愈里那套同源、只是这里不发 LLM。"""
    try:
        from src.parsers.infra.table_recall import select_table
        pick = select_table(tables, code, year, _FIELD_SIG.get(field, "revenue"))
    except Exception:
        return None
    if not pick or not pick.get("table"):
        return None
    v0 = _reparse_forced(code, year, field, pick, tables, pdf)
    s0 = field_plausibility(spec, v0 or {}, anchors or {})
    if s0.get("confidence") == "high":                            # 单张已过锚(理论上不会到这) → 不管
        return None
    _, val, sig, stitched = _stitch_for_anchor(
        code, year, field, pick, tables, pdf, anchors or {}, v0, s0)
    if stitched:
        return {"value": val, "sig": sig, "version": "base+跨页拼接",
                "sweep": [], "stitched_pages": stitched}
    return None


def run_field(code: str, year: int, field: str, anchors: Dict = None,
              tables=None, pdf: str = None, use_llm: bool = False) -> Dict:
    """跑一个字段到出口。确定性部分不发 LLM；use_llm=True 才补 verify/judge_diagnose。
    outcome ∈ green | non_green | no_anchor | no_data | no_input | out_of_scope | no_such_table
             （use_llm 时 green 细化为 committed/verify_hold，non_green 细化为 diagnosis 结论）。"""
    from src.eval.test_store import new_run_id
    run_id = new_run_id()
    spec = get_spec(field)
    from src.eval.domain_scope import is_revenue_breakdown_out_of_scope, out_of_scope_reason
    if is_revenue_breakdown_out_of_scope(code, field):
        out = {"field": field, "outcome": "out_of_scope", "via": "domain",
               "reason": out_of_scope_reason(code, field), "run_id": run_id}
        _emit_event_safe(run_id, code, year, field, "pipeline", "domain_scope",
                         outcome="out_of_scope", payload={"summary": out["reason"], "output": {"reason": out["reason"]}})
        return out
    prep: Dict = {}
    if tables is None or pdf is None:
        prep = _ensure_report_input(code, year)
        _emit_event_safe(run_id, code, year, field, "pipeline", "input_ready",
                         outcome="ok" if prep.get("tables") is not None else "fail",
                         payload={"summary": prep.get("reason") or "输入已就绪",
                                  "output": {"reason": prep.get("reason"),
                                             "downloaded": prep.get("downloaded"),
                                             "tables_n": len(prep.get("tables") or []) if isinstance(prep.get("tables"), list) else None}})
        if tables is None:
            tables = prep.get("tables")
        if pdf is None:
            pdf = prep.get("pdf")
    # 拿到跨表锚
    if anchors is None:                                       # 单跑(endpoint)时自动取锚
        try:
            from src.eval.anchors import get_anchors
            anchors = get_anchors(code, year) or {}
        except Exception:
            anchors = {}
    if not pdf:
        return {"field": field, "outcome": "no_input",
                "reason": prep.get("reason") or "无PDF", "run_id": run_id}
    if not tables:
        return {"field": field, "outcome": "no_input",
                "reason": prep.get("reason") or "未抽表",
                "run_id": run_id,
                **({"downloaded": True} if prep.get("downloaded") else {})}

    # ① 冷启动(主路径:default.py + 版本池 + 跨页拼接)。路由**不再抢跑**——认证解析器是自愈的产物,
    #    在下面自愈级联的第 0 步(按选中表骨架复用)被消费,而不是在流水线最前面预判(旧设计已删)。
    pv = _parse_versioned(code, year, field, tables, pdf, anchors, spec)
    value, sig = pv["value"], pv["sig"]
    _emit_event_safe(run_id, code, year, field, "pipeline", "cold_parse", outcome="ok" if value else "no_data",
                     payload={"summary": "冷启动解析完成" if value else "冷启动无结果",
                              "output": {"rule_version": pv.get("version"),
                                         "stitched_pages": pv.get("stitched_pages"),
                                         "version_sweep_n": len(pv.get("sweep") or []),
                                         "value_stats": _value_stats(value)}})
    conf = sig.get("confidence") if value else None
    rec = {"field": field, "via": "cold", "confidence": conf, "anchored": sig.get("anchored"),
           "value": value, "rule_version": pv.get("version"), "version_sweep": pv.get("sweep"), "run_id": run_id}
    if value:
        _emit_event_safe(run_id, code, year, field, "pipeline", "anchor_check", outcome=conf,
                         payload={"summary": f"锚判={conf}",
                                  "input": {"value_stats": _value_stats(value)},
                                  "output": {"anchored": sig.get("anchored"), "confidence": conf}})
        if conf == "high":                                      # 绿灯：锚确认 → 复核 → 入库
            rec["outcome"] = "green"
            if use_llm:
                rec.update(_green_llm(code, year, field, value, sig, spec))
                _emit_llm_events(run_id, code, year, field, rec)
            if rec.get("outcome") == "committed":
                _emit_event_safe(run_id, code, year, field, "pipeline", "committed",
                                 outcome="ok", payload={"summary": "复核通过并入库", "output": {"via": rec.get("via")}})
            elif rec.get("handed_to_human"):
                _emit_event_safe(run_id, code, year, field, "pipeline", "human",
                                 outcome="needs_human", payload={"summary": rec.get("summary"), "output": {"reason": rec.get("summary")}})
            return rec

    # ② 冷启动没绿灯(无结果 / 不过锚)→ 自愈级联
    #    heal-step-0:按选中表骨架复用已认证解析器(codegen/人修好的成果)。命中且过双闸 → 秒入库,不重造。
    #    这类版式(如 300014 IFRS 矩阵表)default.py 本就解不了,认证解析器是唯一能解的——正是自愈该消费它的地方。
    if use_llm and spec.anchor_key:
        reuse = _routed_reuse(code, year, field, spec, anchors)
        if reuse and reuse.get("outcome") == "committed":
            _emit_event_safe(run_id, code, year, field, "pipeline", "routed", outcome="ok",
                             payload={"summary": "自愈复用认证解析器",
                                      "output": {"parser": reuse.get("reused_parser")}})
            _emit_llm_events(run_id, code, year, field, reuse)
            _emit_event_safe(run_id, code, year, field, "pipeline", "committed",
                             outcome="ok", payload={"summary": "复核通过并入库", "output": {"via": reuse.get("via")}})
            return {**reuse, "run_id": run_id}

    if not value:
        return {"field": field, "outcome": "no_data", "via": "cold", "run_id": run_id}   # 解析器没解出东西 → needs_write
    if not spec.anchor_key:                                     # 无锚字段：确定性判不了对错
        rec["outcome"] = "no_anchor"
        return rec
    rec["outcome"] = "non_green"                                # 有锚却没过 → 需诊断/人工
    if use_llm:
        rec.update(_nongreen_llm(code, year, field))
        _emit_llm_events(run_id, code, year, field, rec)
    if rec.get("outcome") == "committed":
        _emit_event_safe(run_id, code, year, field, "pipeline", "committed",
                         outcome="ok", payload={"summary": "自愈后入库", "output": {"via": rec.get("via")}})
    elif rec.get("handed_to_human"):
        _emit_event_safe(run_id, code, year, field, "pipeline", "human",
                         outcome="needs_human", payload={"summary": rec.get("summary"), "output": {"reason": rec.get("summary")}})
    return rec


# L3 抽表自愈值得一试的失败类:抽碎/漏行都在这几类。全空=整表没抽出;单维不齐=某维漏行(另一维虽≈锚
# 但 best 掩盖了它);严重缺/中度缺=解出<50%/50~90%锚。它们的共性是"表可能选对了、只是 pdfplumber 抽碎"→换参重抽有救。
_L3_CATS = ("严重缺", "中度缺", "全空", "单维不齐")
# codegen(终极层)值得一试的失败类:便宜 healer(选表/L2/L3抽表)都救不了、需**改解析逻辑**的——
# 过计(嵌套/矩阵表堆桶)、严重/中度缺、单维不齐(行式 parser 读不了的结构,如 IFRS-15 矩阵表 300014)。
_CODEGEN_CATS = _L3_CATS + ("过计",)


def _chosen_for(code, year, field) -> Dict:
    """取当前选中表(供 L3 抽表自愈定位页码);select_table 已用锚验过。"""
    from src.parsers.infra.table_recall import select_table
    sel = select_table(get_tables(code, year), code, year, _FIELD_SIG.get(field, "revenue")) or {}
    return {"page": sel.get("page"), "caption": sel.get("caption"), "table": sel.get("table")}


def _green_llm(code, year, field, value, sig, spec) -> Dict:
    """绿灯 → 复核；pass → 入库；复核判"选错表" → 选表自愈重选重解析再复核；其它 hold → 交人工。"""
    try:
        from src.agents.llm_judge import verify_field
        from src.eval.triage_queue import _auto_commit, enqueue
        v = verify_field(field, code, year, value, sig=sig, spec=spec, debug=True)
        verdict = v.get("verdict")
        suspects = v.get("suspects") or v.get("issues") or []
        chat = {"system": v.get("_system"), "prompt": v.get("_prompt"), "reply": v.get("_raw")}
        llm = {"llm_kind": "verify", "verdict": verdict, "suspects": suspects,
               "summary": v.get("summary"), "chat": chat}
        if verdict == "pass":
            return {"outcome": "committed", "committed": _auto_commit(code, year, field, value, sig), **llm}
        # heal-step-0(最便宜的 healer):复核 hold → 先按选中表骨架复用已认证解析器(codegen/人修好的成果),
        # 命中且过双闸 → 秒入库,不必再跑选表自愈/steward/L3。300014 这类"default.py 解出7行但复核否"正靠它。
        reuse = _routed_reuse(code, year, field, spec, None)
        if reuse and reuse.get("outcome") == "committed":
            return {**llm, "reused_after_hold": True, **reuse}
        if any(s.get("issue") == "wrong_table" for s in suspects):     # 复核喊选错表 → 选表自愈
            rec, healed = _heal_and_verify(code, year, field, spec)
            if healed:
                return {**llm, **rec}                                  # 留第一次复核 verdict/suspects + 自愈结果
            heal = rec.get("heal") or {}
            chosen_tbl = heal.pop("_chosen_table", None)               # 别让选中表网格进 DB
            if heal.get("outcome") == "still_bad" and chosen_tbl:      # 选对表但解不出 → L2 改规则自愈
                rh_rec = _rule_heal_and_verify(code, year, field, chosen_tbl, spec)
                if rh_rec:
                    return {**llm, **rh_rec, "heal_probe": heal}
                cat = _diagnose_category(code, year, field)
                if cat in _L3_CATS:
                    eh_rec = _extract_heal_and_verify(code, year, field, chosen_tbl, spec)
                    if eh_rec:
                        return {**llm, **eh_rec, "heal_probe": heal, "routed_cat": cat}
            enqueue(code, year, field, "needs_human", note="选表自愈未选到更好的表")
            return {"outcome": "verify_hold", "handed_to_human": True, **llm, **rec}
        issues = ", ".join(str(s.get("issue")) for s in suspects if s.get("issue"))[:120]
        # 管家A·二次裁决:金额锚已过、弱模型(deepseek)复核 hold(且非选错表)→ 强模型(qwen)重判。
        # 假 hold(强模型也说没问题)→ 入库(仍是金额锚+强模型复核双过,没绕闸);真 hold → 带强模型病因继续修/交人工。
        from src.agents.steward_agent import steward_adjudicate
        adj = steward_adjudicate(code, year, field, value, sig, spec)
        if adj.get("decision") == "commit":
            return {"outcome": "committed", "committed": _auto_commit(code, year, field, value, sig),
                    "steward": adj, **llm}
        # 真 hold(强模型确认):抽表可修类先试 L3,再不行带强模型病因交人工。
        cat = _diagnose_category(code, year, field)
        if cat in _L3_CATS:
            eh_rec = _extract_heal_and_verify(code, year, field, _chosen_for(code, year, field), spec)
            if eh_rec and eh_rec.get("outcome") == "committed":
                return {**llm, **eh_rec, "steward": adj, "routed_cat": cat}
        note = (f"真hold(强模型确认): {adj.get('cause') or issues}" if adj.get("decision") == "real_hold"
                else f"verify hold: {issues or llm['summary'] or ''}")
        enqueue(code, year, field, "needs_human", note=note[:200])
        return {"outcome": "verify_hold", "handed_to_human": True, "steward": adj, **llm}
    except Exception as e:
        return {"llm_error": str(e)[:100]}


def _routed_reuse(code, year, field, spec, anchors) -> Optional[Dict]:
    """自愈第 0 步:按**选中表骨架**查已认证解析器(codegen/人修好的成果)→ 命中就跑它,过**双闸**
    (金额锚 high + 复核 pass,和其它 healer 同一把尺)→ 入库。这是最便宜的 healer:复用而非重造。
    认证解析器是自愈的持久记忆(某版式以前被修好过一次),所以在自愈级联里消费,而不是在流水线最前面预判。
    没命中 / 没过双闸 → None,交给下游 healer(选表自愈/L2/L3/codegen)。"""
    if anchors is None:                                            # 金额锚 high 判定必需,缺则自取
        try:
            from src.eval.anchors import get_anchors
            anchors = get_anchors(code, year) or {}
        except Exception:
            anchors = {}
    try:
        from src.parsers.revenue_router import route_field, field_plausibility
        rt = route_field(spec, code, year)
    except Exception:
        return None
    if rt.get("status") != "routed":
        return None
    value = rt.get("result")
    if not value:
        return None
    sig = field_plausibility(spec, value, anchors or {})           # route 内部只判了 clean,这里补金额锚 high
    if sig.get("confidence") != "high":
        return None
    try:
        from src.agents.llm_judge import verify_field
        from src.eval.triage_queue import _auto_commit
        v = verify_field(field, code, year, value, sig=sig, spec=spec)
    except Exception:
        return None
    if v.get("verdict") != "pass":
        return None
    return {"field": field, "via": "routed(自愈复用)", "outcome": "committed",
            "confidence": "high", "value": value,
            "committed": _auto_commit(code, year, field, value, sig),
            "reused_parser": rt.get("parser"),
            "llm_kind": "verify", "verdict": "pass", "summary": v.get("summary")}


def _diagnose_category(code, year, field) -> str:
    """诊断路由器:把非绿灯归到哪一类(复用失败分析的确定性归类 `_classify_failure`)。
    用 heal_debug 的逐维分项和 + 锚。无 verify(此刻还没跑复核),故只用确定性信号。"""
    try:
        from src.console_service import heal_debug
        d = heal_debug(code, year, field) or {}
    except Exception:
        d = {}
    anchor = d.get("anchor")
    per_dim = d.get("dims") or []
    ndim = len([x for x in per_dim if x.get("sum")])
    best = (max((x.get("sum") or 0) for x in per_dim) / anchor) if (anchor and per_dim) else 0
    return _classify_failure("non_green", "", {}, anchor, per_dim, best, ndim)


def _run_diagnose(code, year, field, heal_probe=None) -> Dict:
    """跑 judge_diagnose 表层诊断(链尽时的出口)。"""
    from src.agents.judge_diagnose_agent import prepare_judge_diagnose
    from src.console_service import judge_chat
    prep = prepare_judge_diagnose(code, year, field)
    if prep.get("error"):
        return {"llm_error": prep["error"]}
    res = judge_chat(code, year, field, prep["messages"])
    msgs = prep.get("messages") or [{}, {}]
    out = {"llm_kind": "diagnose", "decision": res.get("decision"),
           "root_cause": res.get("root_cause"), "next_action": res.get("next_action"),
           "evidence": res.get("evidence"), "summary": res.get("summary"),
           "handed_to_human": res.get("handed_to_human"),
           "diag_chat": {"system": (msgs[0] or {}).get("content"),
                         "prompt": (msgs[1] or {}).get("content"), "reply": res.get("reply")}}
    if heal_probe:
        out["heal_probe"] = heal_probe
    return out


def _retry_after_anchor_heal(code, year, field, spec, tables, pdf) -> Optional[Dict]:
    """无锚类:取锚自愈后重跑冷启动+LLM 链(拿到营收锚才可能变绿灯/进 healer)。"""
    from src.eval.anchors import heal_anchors
    healed = heal_anchors(code, year)
    if not healed.get("revenue"):
        return None
    pv = _parse_versioned(code, year, field, tables, pdf, healed, spec)
    value, sig = pv["value"], pv["sig"]
    if not value:
        return None
    conf = sig.get("confidence")
    base = {"field": field, "via": "cold", "confidence": conf, "anchored": sig.get("anchored"),
            "value": value, "rule_version": pv["version"], "version_sweep": pv["sweep"],
            "anchor_healed": True}
    if conf == "high":
        base["outcome"] = "green"
        base.update(_green_llm(code, year, field, value, sig, spec))
        return base
    base["outcome"] = "non_green"
    extra = _nongreen_llm(code, year, field, skip_no_anchor=True)
    return {**base, **extra}


def _nongreen_llm(code, year, field, skip_no_anchor: bool = False) -> Dict:
    """非绿灯 → **按诊断归类派 healer**(Tier 1 路由器):
      过计 → 直接 L2 切桶(选表自愈救不了过计);无锚 → 直接诊断(选表/L2 都靠锚,没锚白跑,取锚自愈 Phase2 再接);
      其余(选错表/全空/严重缺/单维不齐/…)→ 选表自愈优先 → still_bad 则 L2 → 仍不行诊断。
    每个 healer 内部仍走过锚+复核双闸。结果带 routed_cat(诊断归类,前端可见)。"""
    try:
        spec = get_spec(field)
        tables, pdf = get_tables(code, year), _pdf(code, year)
        cat = _diagnose_category(code, year, field)

        # 无锚:金融股→域外;否则取锚自愈→重跑;仍无锚→诊断
        if cat == "无锚" and not skip_no_anchor:
            from src.eval.domain_scope import is_revenue_breakdown_out_of_scope, out_of_scope_reason
            if is_revenue_breakdown_out_of_scope(code, field):
                return {"outcome": "out_of_scope", "via": "domain", "routed_cat": "金融域外",
                        "reason": out_of_scope_reason(code, field)}
            retry = _retry_after_anchor_heal(code, year, field, spec, tables, pdf)
            if retry:
                return retry
            out = _run_diagnose(code, year, field)
            out["routed_cat"] = cat
            out["note"] = "取锚自愈后仍无营收锚"
            return out

        # 过计(某桶 >锚,多维堆一桶/合计混入):**先试 L2 切桶**(选表自愈换表救不了"堆桶");
        # L2 入库就收工,没入库再落回选表自愈级联(过计也可能是"选错了一张更大的表",那才靠选表自愈)。
        if cat == "过计":
            from src.parsers.infra.table_recall import select_table
            pick = select_table(get_tables(code, year), code, year, _FIELD_SIG.get(field, "revenue"))
            if pick and pick.get("table"):
                rh = _rule_heal_and_verify(code, year, field, pick, spec)
                if rh and rh.get("outcome") == "committed":
                    return {**rh, "routed_cat": cat}

        # 通用级联:选表自愈优先 → still_bad L2 → 诊断
        rec, healed = _heal_and_verify(code, year, field, spec)
        if healed:
            return {**rec, "routed_cat": cat}
        heal = rec.get("heal") or {}
        chosen_tbl = heal.pop("_chosen_table", None)
        if heal.get("outcome") == "no_pick":
            from src.eval.triage_queue import enqueue
            enqueue(code, year, field, "needs_human", note="no_such_table: 源文无营收构成表")
            return {"llm_kind": "no_such_table", "outcome": "no_such_table",
                    "handed_to_human": True, "heal_probe": heal, "routed_cat": cat}
        # committed 立刻收；L2/L3 的 verify_hold 先攒着(pending)——codegen 这终极层也不成才回退它。
        pending = None
        if heal.get("outcome") == "still_bad" and chosen_tbl:
            rh_rec = _rule_heal_and_verify(code, year, field, chosen_tbl, spec)
            if rh_rec and rh_rec.get("outcome") == "committed":
                return {**rh_rec, "heal_probe": heal, "routed_cat": cat}
            if rh_rec and rh_rec.get("outcome") == "verify_hold":
                pending = {**rh_rec, "heal_probe": heal, "routed_cat": cat}
            if cat in _L3_CATS:
                eh_rec = _extract_heal_and_verify(code, year, field, chosen_tbl, spec)
                if eh_rec and eh_rec.get("outcome") == "committed":
                    return {**eh_rec, "heal_probe": heal, "rule_heal_probe": rh_rec, "routed_cat": cat}
                if eh_rec:
                    pending = {**eh_rec, "heal_probe": heal, "rule_heal_probe": rh_rec, "routed_cat": cat}
        # 终极层:需改解析逻辑类(过计/矩阵/漏行)→ codegen 写专用解析器(双闸+固化注册)。committed 就收。
        if cat in _CODEGEN_CATS:
            cg = _codegen_and_verify(code, year, field, spec)
            if cg and cg.get("outcome") == "committed":
                return {**cg, "heal_probe": heal, "routed_cat": cat}
        if pending:                                       # codegen 没成 → 回退 L2/L3 的 verify_hold
            return pending
        return {**_run_diagnose(code, year, field, rec.get("heal")), "routed_cat": cat}
    except Exception as e:
        return {"llm_error": str(e)[:100]}


_ANCHOR_KEY = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd_expense"}


def _reparse_forced(code, year, field, chosen_item, tables, pdf):
    """用 LLM 选中的表强制重解析（绕过 select_table，走解析器的 forced_sel 口子）。"""
    from src.engine_orchestrator import FinParseAI
    from src.parsers.infra.table_recall import _best_col_vs_anchor
    from src.eval.anchors import get_anchors
    anc = (get_anchors(code, year) or {}).get(_ANCHOR_KEY.get(field, "revenue"))
    amount_col = None
    if anc:
        amount_col, _ = _best_col_vs_anchor(chosen_item.get("table") or [], anc)
    sel = {**chosen_item, "amount_col": amount_col, "via": "llm_select"}
    try:
        parser = FinParseAI()._get_parser(field, pdf)
        return parser.parse(pdf, pre_scan=tables, code=code, year=year, forced_sel=sel).get(field)
    except TypeError:
        return None                                   # 该字段解析器还没开 forced_sel 口子(暂只营收)


def _overanchor_candidates(code, year, field, spec, tables, pdf, anchors, exclude_pages, k=4):
    """召回候选里:重解后**过锚**(high)、**非坑表**、未试过的,最多 k 个 —— 给选表多轮当备选表。
    (过锚只是必要条件,不充分:如'期末未分配利润'某列凑巧≈锚。所以每个还要过复核,由调用方做。)"""
    from src.parsers.infra.table_recall import vector_recall, _is_ten_pct_trap
    out = []
    for t in vector_recall(tables, _FIELD_SIG.get(field, "revenue"), top_k=12, threshold=0.0)[:12]:
        if t.get("page") in exclude_pages or _is_ten_pct_trap(t):
            continue
        v = _reparse_forced(code, year, field, t, tables, pdf)
        if field_plausibility(spec, v or {}, anchors).get("confidence") == "high":
            out.append(t)
            if len(out) >= k:
                break
    return out


def _finalize_heal_chosen(code, year, field, chosen, tables, pdf, anchors, meta: Dict) -> Dict:
    """选中表 → forced 重解析 → 过锚 → 必要时跨页拼。"""
    value = _reparse_forced(code, year, field, chosen, tables, pdf)
    sig = field_plausibility(get_spec(field), value or {}, anchors)
    stitched = None
    if sig.get("confidence") != "high":
        chosen, value, sig, stitched = _stitch_for_anchor(
            code, year, field, chosen, tables, pdf, anchors, value, sig)
    conf = sig.get("confidence")
    caliber_gap = meta.get("caliber_gap")
    outcome = "green" if conf == "high" else ("caliber_gap" if caliber_gap else "still_bad")
    from src.agents.llm_judge import _grid_to_text
    return {"healed": True, "outcome": outcome,
            "chosen_page": meta.get("chosen_page", chosen.get("page")),
            "chosen_caption": meta.get("chosen_caption", chosen.get("caption")),
            "select_stage": meta.get("select_stage"), "select_reason": meta.get("select_reason"),
            "caliber_gap": caliber_gap, "value": value, "confidence": conf,
            "select_chat": meta.get("select_chat"), "stitched_pages": stitched,
            "_chosen_table": chosen, "source": _grid_to_text(chosen.get("table") or [])}


def heal_select(code, year, field="revenue_breakdown") -> Dict:
    """选表自愈：先向量 top-N 扫锚(无 LLM) → 再 LLM 认表 → forced 重解析 → 过锚。
    outcome ∈ green | caliber_gap | still_bad | no_pick。"""
    from src.agents.select_table_agent import select_table_llm
    from src.eval.anchors import get_anchors
    tables, pdf = get_tables(code, year), _pdf(code, year)
    anchors = get_anchors(code, year) or {}
    spec = get_spec(field)

    for cand in _overanchor_candidates(code, year, field, spec, tables, pdf, anchors, set(), k=8):
        out = _finalize_heal_chosen(code, year, field, cand, tables, pdf, anchors, {
            "select_stage": "sweep_topn", "select_reason": "向量 top-N 过锚(未发 LLM)"})
        if out.get("outcome") in ("green", "caliber_gap"):
            return out

    sr = select_table_llm(code, year, field, debug=True)
    select_chat = {"prompt": sr.get("_prompt"), "reply": sr.get("_raw")}
    chosen = sr.get("chosen_table")
    if not chosen or not chosen.get("table"):
        return {"healed": False, "outcome": "no_pick", "select_reason": sr.get("reason"),
                "select_chat": select_chat}
    return _finalize_heal_chosen(code, year, field, chosen, tables, pdf, anchors, {
        "chosen_page": sr.get("chosen_page"), "chosen_caption": sr.get("chosen_caption"),
        "select_stage": sr.get("stage"), "select_reason": sr.get("reason"),
        "caliber_gap": sr.get("caliber_gap"), "select_chat": select_chat})


def _stitch_for_anchor(code, year, field, chosen, tables, pdf, anchors, value0, sig0):
    """跨页续表拼接(锚闸兜底):选中表不过锚时,把它物理紧邻的续表逐张并进来重解,
    一旦过锚就采纳合并表。合并不过锚就不采纳(锚闸=安全,不怕误召续表)。
    返回 (最终选中表, value, sig, stitched_pages)。没拼/没帮助 → 返回原样、stitched=None。"""
    from src.parsers.infra.table_recall import following_tables, merge_tables
    conts = following_tables(tables, chosen)
    if not conts:
        return chosen, value0, sig0, None
    merged = chosen
    pages = [chosen.get("page")]
    for c in conts:
        merged = merge_tables(merged, c)
        pages.append(c.get("page"))
        v = _reparse_forced(code, year, field, merged, tables, pdf)
        s = field_plausibility(get_spec(field), v or {}, anchors)
        if s.get("confidence") == "high":                          # 拼到过锚 → 采纳
            return merged, v, s, pages
    return chosen, value0, sig0, None                              # 拼了也不过锚 → 不采纳


# 选表自愈后第二次复核的信任提示:选表 agent 已确认表 → 别再重判选表,只逐项核数据
_TRUST_NOTE = ("\n【选表已确认】上面这张表是选表 agent 从全表里认定的**正确营业收入构成表**"
               "(已排除销售表/分部表/毛利率表等)。因此**不要再判 wrong_table,也不要因"
               "'合计略小于营业收入 / 维度少'就判不完整/跨页截断**——分行业/分产品合计略小于营收"
               "通常是'其他业务收入'不按此维度拆分(主营业务口径),属正常。你只需**逐项核对数据**"
               "(名称有没有抠错、金额有没有取错列/单位),逐项对得上就 pass。\n")


def _heal_and_verify(code, year, field, spec):
    """选表自愈(**多轮**):LLM 首选 → 过锚+复核双闸;没入库 → 逐个试召回里其它过锚候选(去坑表),
    每个都走复核,第一个**双闸都过**就入库。都不过 → 交回(still_bad 给 L2 / no_pick 给 no_such_table)。
    返回 (rec, healed)。"""
    from src.agents.llm_judge import verify_field, _grid_to_text
    from src.eval.triage_queue import _auto_commit
    from src.eval.anchors import get_anchors
    tables, pdf = get_tables(code, year), _pdf(code, year)
    anchors = get_anchors(code, year) or {}
    heal = heal_select(code, year, field)
    src0 = heal.pop("source", None)
    if src0:
        heal["source_preview"] = "\n".join(src0.split("\n")[:24])
    ho = heal.get("outcome")
    tried = set()

    def _verify_commit(value, table, heal_info):
        """一个过锚候选走复核(信任源文,只核数据);pass→入库返回 rec,否则 None(hold)。"""
        s = _grid_to_text(table.get("table") or [])
        v2 = verify_field(field, code, year, value, spec=spec, source_override=s,
                          extra_note=_TRUST_NOTE, debug=True)
        rd = {"verdict": v2.get("verdict"), "suspects": v2.get("suspects") or [], "summary": v2.get("summary")}
        common = {"llm_kind": "verify", "healed_select": True, "heal": heal_info,
                  "reverify": v2.get("verdict"), "reverify_detail": rd,
                  "reverify_chat": {"system": v2.get("_system"), "prompt": v2.get("_prompt"), "reply": v2.get("_raw")}}
        if v2.get("verdict") == "pass":
            return {"outcome": "committed", "caliber_gap": heal_info.get("caliber_gap"),
                    "committed": _auto_commit(code, year, field, value, {"confidence": "high"}), **common}
        return None

    # ① LLM 首选(过锚/口径差)先试
    if ho in ("green", "caliber_gap") and heal.get("value"):
        tried.add(heal.get("chosen_page"))
        rec = _verify_commit(heal["value"], heal.get("_chosen_table") or {}, heal)
        if rec:
            return rec, True

    # ② 多轮:逐个试召回里其它过锚候选,每个走复核(第一个复核 pass 就收工)
    for cand in _overanchor_candidates(code, year, field, spec, tables, pdf, anchors, tried):
        tried.add(cand.get("page"))
        value = _reparse_forced(code, year, field, cand, tables, pdf)
        info = {"outcome": "green", "chosen_page": cand.get("page"), "chosen_caption": cand.get("caption"),
                "select_stage": "multi_round", "value": value, "_chosen_table": cand,
                "source_preview": "\n".join(_grid_to_text(cand.get("table") or []).split("\n")[:24])}
        rec = _verify_commit(value, cand, info)
        if rec:
            return rec, True

    # ③ 都不过 → 交回:LLM 选到了表(过锚但复核都否决)当 still_bad 给 L2;否则 no_pick
    if ho in ("green", "caliber_gap"):
        heal["outcome"] = "still_bad"
    return {"heal": heal}, False


def _rule_heal_and_verify(code, year, field, chosen, spec) -> Optional[Dict]:
    """L2 改规则自愈:选对表但 base 解错 → LLM 提规则 delta → 合并重解过锚 → 复核(信任源文) →
    pass 则入库 + save_version 把这条规则固化进池;否则交人工。
    返回 rec(dict)：outcome ∈ committed / verify_hold；None 表示 delta 也修不了(交回诊断/人工)。"""
    from src.agents.rule_heal_agent import rule_heal
    from src.agents.llm_judge import verify_field, _grid_to_text
    from src.eval.triage_queue import _auto_commit, enqueue
    rh = rule_heal(code, year, field, chosen, debug=True)
    common = {"llm_kind": "rule_heal",
              "rule_heal": {k: rh.get(k) for k in
                            ("outcome", "delta", "note", "reason", "rounds_used", "dim_diff_after")},
              "rule_heal_chat": rh.get("chat")}
    if not rh.get("ok"):
        return None                                                    # 加规则也修不了 → 交回上层诊断
    # 过锚了 → 走复核(信任这张已选对的表,只逐项核数据)。入库条件与第一/二次一致。
    src = _grid_to_text(chosen.get("table") or [])
    v2 = verify_field(field, code, year, rh["value"], spec=spec,
                      source_override=src, extra_note=_TRUST_NOTE, debug=True)
    common["reverify"] = v2.get("verdict")
    common["reverify_detail"] = {"verdict": v2.get("verdict"), "suspects": v2.get("suspects") or [],
                                 "summary": v2.get("summary")}
    common["reverify_chat"] = {"system": v2.get("_system"), "prompt": v2.get("_prompt"), "reply": v2.get("_raw")}
    if v2.get("verdict") == "pass":
        vid = f"rev_heal_{code}_{year}"
        from src.parsers.infra.consolidate import consolidate_rule_delta
        path = consolidate_rule_delta(_RULE_FILE.get(field, "revenue"), vid, rh["delta"],
                                        note=rh.get("note"), meta={"origin": f"{code}_{year}"})
        return {"outcome": "committed", "healed_rule": True,
                "committed": _auto_commit(code, year, field, rh["value"], {"confidence": "high"}),
                "rule_version_saved": vid, "rule_version_path": path, **common}
    enqueue(code, year, field, "needs_human", note=f"L2改规则过锚但复核hold p{chosen.get('page')}"[:200])
    return {"outcome": "verify_hold", "handed_to_human": True, **common}


def _extract_heal_and_verify(code, year, field, chosen, spec) -> Optional[Dict]:
    """L3 抽表自愈:表选对但 pdfplumber 漏行 → 换参重抽该页 → 过锚 → 复核 → 入库 + 固化抽表配置。
    返回 rec: outcome ∈ committed / verify_hold; None 表示换参仍修不了。"""
    from src.agents.extract_heal_agent import extract_heal
    from src.agents.llm_judge import verify_field, _grid_to_text
    from src.eval.triage_queue import _auto_commit, enqueue
    from src.parsers.infra.consolidate import consolidate_extract_profile
    eh = extract_heal(code, year, field, chosen, debug=True)
    common = {"llm_kind": "extract_heal",
              "extract_heal": {k: eh.get(k) for k in
                               ("outcome", "profile", "settings", "reason", "tries")},
              "chosen_page": chosen.get("page")}
    if not eh.get("ok"):
        return None
    pick = eh.get("chosen_table") or chosen
    src = _grid_to_text(pick.get("table") or [])
    v2 = verify_field(field, code, year, eh["value"], spec=spec,
                      source_override=src, extra_note=_TRUST_NOTE, debug=True)
    common["reverify"] = v2.get("verdict")
    common["reverify_detail"] = {"verdict": v2.get("verdict"), "suspects": v2.get("suspects") or [],
                                 "summary": v2.get("summary")}
    common["reverify_chat"] = {"system": v2.get("_system"), "prompt": v2.get("_prompt"), "reply": v2.get("_raw")}
    if v2.get("verdict") == "pass":
        page = pick.get("page") or chosen.get("page")
        path = consolidate_extract_profile(code, year, page, eh.get("profile") or "default",
                                           eh.get("settings") or {},
                                           note=f"extract_heal {code}_{year}")
        from src.eval.extract_profiles import mark_report_profile_synced
        mark_report_profile_synced(code, year, page)
        return {"outcome": "committed", "healed_extract": True,
                "committed": _auto_commit(code, year, field, eh["value"], {"confidence": "high"}),
                "extract_profile_path": path, "profile": eh.get("profile"), **common}
    # roadmap#1:L3 重抽过锚(eh fixed)但弱模型复核 hold → 管家用**强模型**对同一张重抽表二次裁决。
    from src.agents.steward_agent import steward_adjudicate
    adj = steward_adjudicate(code, year, field, eh["value"], eh.get("sig") or {"confidence": "high"}, spec,
                             source_override=src, extra_note=_TRUST_NOTE)
    common["steward"] = adj
    if adj.get("decision") == "commit":            # 强模型判假 hold → 入库(L3 修对了、弱模型固执)
        page = pick.get("page") or chosen.get("page")
        path = consolidate_extract_profile(code, year, page, eh.get("profile") or "default",
                                           eh.get("settings") or {}, note=f"extract_heal {code}_{year}")
        from src.eval.extract_profiles import mark_report_profile_synced
        mark_report_profile_synced(code, year, page)
        return {"outcome": "committed", "healed_extract": True, "steward": adj,
                "committed": _auto_commit(code, year, field, eh["value"], {"confidence": "high"}),
                "extract_profile_path": path, "profile": eh.get("profile"), **common}
    enqueue(code, year, field, "needs_human",
            note=f"L3重抽过锚但复核hold(管家强模型也确认) p{chosen.get('page')}"[:200])
    return {"outcome": "verify_hold", "handed_to_human": True, "steward": adj, **common}


def _codegen_and_verify(code, year, field, spec) -> Optional[Dict]:
    """终极层:便宜 healer 都救不了、需改解析逻辑(过计/矩阵/漏行)→ codegen 写专用解析器。
    generate_parser_autonomous 内部已过**双闸**(金额锚 high + 复核 pass)才 accept;accept 则:
      ① _auto_commit 入库 ② 注册认证解析器(certify + 路由缓存)→ 下次同版式直接 routed 免 LLM。
    None = codegen 没写出过双闸的版本(交回诊断)。⚠️ 现走本进程 sandbox_exec(接无人值守前须上 subprocess 隔离)。"""
    from src.agents.code_generator import generate_parser_autonomous
    from src.eval.triage_queue import _auto_commit
    out_path = f"src/parsers/versions/rev_{code}_{year}.py"
    try:
        r = generate_parser_autonomous(code, year, spec, out_path, max_rounds=8, log=lambda *a: None)
    except Exception as e:
        return None
    if not r.get("accepted"):
        return {"llm_kind": "codegen", "outcome": "codegen_still_bad",
                "codegen": {"rounds": r.get("rounds"), "escalate": r.get("escalate")}}
    committed = _auto_commit(code, year, field, r["value"], r.get("sig") or {"confidence": "high"})
    cert = None
    try:                                        # 固化:按**选中表骨架**登记认证解析器(table_doc = 路由主键;指纹已废弃)
        from src.eval.parser_catalog import certify
        from src.parsers.infra.table_recall import report_table_doc
        _sf = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd"}.get(field, "revenue")
        tdoc = report_table_doc(code, year, _sf)
        # smoke 闸:认证前沙箱跑通这个文件——跑不起来(如作用域 bug)就拒登,免得坏解析器进库、以后 route 永远 needs_repair。
        cert = certify(f"{code}-codegen", out_path, field, table_doc=tdoc or None, smoke=(code, year))
    except Exception:
        pass
    certified_ok = bool(cert and cert.get("certified"))            # smoke 没跑通 → 没登记进认证库(报告已入库,不影响本次结果,但下次不会复用它)
    return {"outcome": "committed", "llm_kind": "codegen", "via": "codegen",
            "committed": committed,
            "certified_parser": out_path if certified_ok else None,
            "cert": cert,
            "value": r["value"], "confidence": "high",
            "codegen": {"rounds": r.get("rounds")}}


def run_report(code: str, year: int, fields: List[str] = None, use_llm: bool = False) -> Dict:
    """一份报告跑全字段。"""
    fields = fields or FIELDS
    tables = get_tables(code, year)
    pdf = _pdf(code, year)
    anchors = {}
    try:
        from src.eval.anchors import get_anchors
        anchors = get_anchors(code, year) or {}
    except Exception:
        pass
    per = [run_field(code, year, f, anchors, tables, pdf, use_llm) for f in fields]
    return {"code": code, "year": year, "fields": per,
            "has_input": bool(tables and pdf)}


def field_chain(code: str, year: int, field: str) -> Dict:
    """把一个字段的整条链路逐阶段拆开 + 给出失败原因（给前端看"链路 + 为什么出问题"）。
    阶段：抽表 → 选表 → 解析 → 锚判(逐维) → 出口。"""
    from src.eval.field_spec import get_spec
    from src.parsers.revenue_router import field_plausibility
    from src.prompts.context.parse import missing_dims
    from src.prompts.context.table import table_preview
    spec = get_spec(field)
    pdf = _pdf(code, year)
    tables = get_tables(code, year)
    stages: List[Dict] = []
    out = {"code": code, "year": year, "field": field, "stages": stages}

    def add(name, ok, detail):
        stages.append({"name": name, "ok": ok, "detail": detail})

    if not pdf or not tables:
        add("抽表", False, "无 PDF 或未扫表")
        return {**out, "outcome": "no_input", "reason": "无 PDF / 未扫表"}
    add("抽表", True, {"n_tables": len(tables), "io": {"in": "PDF 原文", "out": f"{len(tables)} 张表"}})

    # 选表
    try:
        from src.parsers.infra.table_recall import select_table
        pick = select_table(tables, code, year, _FIELD_SIG.get(field, "revenue"))
    except Exception:
        pick = None
    if not pick or not pick.get("table"):
        add("选表", False, "召回/锚都没命中目标表")
        return {**out, "outcome": "no_data", "reason": "选表失败：没选中目标表"}
    add("选表", True, {"page": pick.get("page"), "via": pick.get("via"),
                       "rows": len(pick["table"]), "caption": (pick.get("caption") or "")[:60],
                       "io": {"in": f"{len(tables)} 张候选表",
                              "out": f"p{pick.get('page')}《{(pick.get('caption') or '')[:24]}》· {len(pick['table'])}行"}})

    # 解析（主路径=冷启动:default.py + 版本池 + 跨页拼。认证解析器不再抢跑,降为自愈第0步,只在冷启动没过锚时复用)
    value, prov = _cold_parse_full(code, year, field, tables, pdf)
    if not value:
        add("解析", False, "冷启动在选中表上没解出结构化数据")
        return {**out, "outcome": "no_data", "reason": "解析为空（选中表结构/认列失败）"}
    dims_present = list(value.keys()) if isinstance(value, dict) else ["<list>"]
    rows_per_dim = {k: len(v) for k, v in value.items()} if isinstance(value, dict) else {}
    _out = "、".join(f"{k}:{n}行" for k, n in rows_per_dim.items() if n) or "全空(选中表解不出分项)"
    add("解析", True, {"via": "冷启动", "dims": dims_present,
                       "rows_per_dim": rows_per_dim,
                       "io": {"in": f"选中表(第{pick.get('page')}页那张)", "out": _out}})
    out["value"] = value                                          # 解析出的结构化 JSON

    # 溯源：每个解析值出自哪一页 + 选中表原文（让"数字从哪来"可核）
    prov_items = [{"path": k, "page": v.get("page")} for k, v in prov.items()
                  if isinstance(v, dict) and v.get("page")]
    prov_pages = sorted({it["page"] for it in prov_items})
    out["provenance"] = {"pages": prov_pages, "items": prov_items[:60], "n": len(prov_items)}
    out["source_preview"] = table_preview(pick["table"], max_rows=30, max_cols=14)

    # 锚判（逐维）
    anchors = {}
    try:
        from src.eval.anchors import get_anchors
        anchors = get_anchors(code, year) or {}
    except Exception:
        pass
    sig = field_plausibility(spec, value, anchors)
    conf = sig.get("confidence")
    diag = {}
    try:
        from src.console_service import heal_debug
        diag = heal_debug(code, year, field) or {}
    except Exception:
        pass
    dims = diag.get("dims") or []
    per_dim = [{"dim": d.get("dim"), "sum": d.get("sum"), "match": d.get("match")} for d in dims]
    missing = missing_dims(value) if field == "revenue_breakdown" else []
    anchor_ok = conf == "high"
    _hit = sum(1 for p in per_dim if p.get("match"))
    add("锚判", anchor_ok, {"anchor": diag.get("anchor"), "confidence": conf,
                           "per_dim": per_dim, "missing_dims": missing,
                           "dims_agree": diag.get("dims_agree"),
                           "io": {"in": "各维分项和 + 营收锚",
                                  "out": f"{_hit}/{len(per_dim)} 维过锚 · 置信 {conf}"}})

    # 出口 + 失败原因
    if conf == "high":
        outcome, reason = "green", "冷启动解析过跨表锚 → 绿灯"
    elif not spec.anchor_key:
        outcome, reason = "no_anchor", "该字段无 DB 锚，确定性判不了对错（需复核 agent / 人工）"
    else:
        outcome = "non_green"
        short = [d.get("dim") for d in dims if not d.get("match")]
        if missing or short:
            reason = f"维度不完整：缺失 {missing or '无'}，分项和未过锚 {short or '无'} → 多为跨页续表未拼接/漏行"
        elif diag.get("dims_agree"):
            reason = "各维度互相一致但都不过锚 → 疑似口径差（营业收入 vs 营业总收入）"
        else:
            reason = "维度互相矛盾、都不过锚 → 疑似选错表 / 认列错"
    add("出口", outcome == "green", f"{outcome}")
    return {**out, "outcome": outcome, "reason": reason, "via": "cold"}


def _classify_failure(o, reason, v, anchor, per_dim, best, ndim) -> str:
    """从 DB 已存的 chain(锚判逐维) + verify(复核疑点) 给失败归类——不重解。"""
    susp = (v.get("suspects") or []) + ((v.get("reverify_detail") or {}).get("suspects") or [])
    iss = {s.get("issue") for s in susp if s.get("issue")}
    if o == "no_such_table":
        return "真无表"
    if o == "out_of_scope":
        return "金融域外"
    if not anchor:
        return "无锚"
    if not per_dim or ndim == 0:
        return "全空"
    if best > 1.05:
        return "过计"
    if best < 0.5:
        return "严重缺"
    if "amount_error" in iss:
        return "取错列/单位"
    if "name_error" in iss:
        return "抠错名称"
    if "wrong_table" in iss:
        return "选错表"
    if 0.9 <= best <= 1.03 and ndim >= 2:
        return "单维不齐"
    return "中度缺"


# 类别 → (一句原因, 自愈落点, 能力现状 have/light/new/out)
_CAT_META = {
    "单维不齐":  ("有一维精确=锚,另一维短→被全维闸卡", "≥1维过锚交复核 / 短维补行", "light"),
    "全空":      ("一维都没解出(选表选错/认列全废)", "选表自愈 / L2认列", "have"),
    "无锚":      ("仍取不到营收锚(非金融)", "取锚自愈 / 人工", "light"),
    "金融域外":  ("银行/券商/信托/保险,不适用营收构成", "标 out_of_scope(已达成)", "have"),
    "过计":      ("某桶和 >锚(多维堆一桶/合计混入)", "L2切桶", "have"),
    "选错表":    ("复核判选错表,自愈没救回", "选表自愈多轮", "light"),
    "严重缺":    ("仅解出<50%锚(选到小表/大漏行)", "选表自愈多轮 / 抽表自愈", "new"),
    "取错列/单位": ("金额取错列或单位没换算", "L2扩单位override", "light"),
    "抠错名称":  ("名称列抠错", "L2加name别名", "light"),
    "中度缺":    ("解出50~90%锚(漏行/漏维)", "跨页拼接 / 抽表自愈", "new"),
    "真无表":    ("报告确无营收构成表", "正确判缺失(已达成)", "have"),
}
_CAT_ORDER = ["单维不齐", "全空", "无锚", "金融域外", "过计", "选错表", "严重缺", "取错列/单位", "抠错名称", "中度缺", "真无表"]


def failure_analysis(year: int = 2025, field: str = "revenue_breakdown") -> Dict:
    """从 DB 汇成"逐份失败分析"(供前端实时页):每份非 committed 的结局/类别/占锚/LLM是否跑成。
    全部读已存的 chain+verify,不重解 → 秒回。"""
    from src.eval.test_store import list_latest_runs
    runs = [r for r in list_latest_runs(year, [field]) if r["field"] == field]
    tally = Counter()
    items = []
    for r in runs:
        o = r["outcome"]
        tally[o] += 1
        if o == "committed":
            continue
        v = r.get("verify") or {}
        ch = r.get("chain") or {}
        anchor, per_dim = None, []
        for s in (ch.get("stages") or []):
            d = s.get("detail")
            if s.get("name") == "锚判" and isinstance(d, dict):
                anchor, per_dim = d.get("anchor"), (d.get("per_dim") or [])
        ndim = len([d for d in per_dim if d.get("sum")])
        best = (max((d.get("sum") or 0) for d in per_dim) / anchor) if (anchor and per_dim) else 0
        cat = _classify_failure(o, ch.get("reason"), v, anchor, per_dim, best, ndim)
        items.append({"code": r["stock_code"], "outcome": o, "llm_ran": bool(v),
                      "best": round(best, 2), "ndim": ndim, "cat": cat,
                      "reason": (ch.get("reason") or "")[:120]})
    by_cat = Counter(x["cat"] for x in items)
    cats = [{"cat": c, "n": by_cat[c], **dict(zip(("why", "heal", "tag"), _CAT_META[c]))}
            for c in _CAT_ORDER if by_cat.get(c)]
    items.sort(key=lambda x: (_CAT_ORDER.index(x["cat"]) if x["cat"] in _CAT_ORDER else 99, x["code"]))
    committed = tally.get("committed", 0)
    denom = (sum(tally.values()) - tally.get("no_such_table", 0) - tally.get("no_input", 0)
             - tally.get("out_of_scope", 0) - by_cat.get("无锚", 0) - by_cat.get("金融域外", 0))
    return {"year": year, "field": field, "total": sum(tally.values()),
            "committed": committed, "success_rate": round(committed / denom, 3) if denom else None,
            "denom": denom, "n_fail": len(items), "llm_missed": sum(1 for x in items if not x["llm_ran"]),
            "tally": dict(tally), "cats": cats, "items": items}


_RESULT_PATH = "goldset/pipeline_result.json"


def save_result(res: Dict, path: str = _RESULT_PATH) -> None:
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)


def load_result(path: str = _RESULT_PATH) -> Optional[Dict]:
    import json
    import os
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _rate(cnt: Counter) -> Dict:
    # no_such_table / out_of_scope = 数据不适用或不存在 → 剔出分母(不算失败)
    anchored = cnt["green"] + cnt["committed"] + cnt["verify_hold"] + cnt["non_green"] + cnt["no_data"]
    success = cnt["green"] + cnt["committed"]      # 过锚且未被复核否决（复核跑完后 green→0，只剩 committed）
    return {"green": cnt["green"], "committed": cnt["committed"], "verify_hold": cnt["verify_hold"],
            "non_green": cnt["non_green"], "no_data": cnt["no_data"],
            "no_anchor": cnt["no_anchor"], "no_input": cnt["no_input"],
            "no_such_table": cnt["no_such_table"], "out_of_scope": cnt["out_of_scope"],
            "success_rate": round(success / anchored, 3) if anchored else None,
            "anchored_denominator": anchored}


def _aggregate(reports: List[Dict], fields: List[str], year: int, use_llm: bool = False) -> Dict:
    """把逐报告结果汇成成功率（字段级 + 整体）。绿灯/(绿+非绿+无数据)。"""
    by_field = {f: Counter() for f in fields}
    for r in reports:
        for fr in r["fields"]:
            if fr["field"] in by_field:
                by_field[fr["field"]][fr["outcome"]] += 1
    overall = Counter()
    for f in fields:
        overall.update(by_field[f])
    return {"n_reports": len(reports), "year": year, "use_llm": use_llm,
            "overall": _rate(overall), "by_field": {f: _rate(by_field[f]) for f in fields},
            "reports": reports}


def analyze_batch(codes: List[str], year: int = 2025, fields: List[str] = None,
                  use_llm: bool = False, log=print) -> Dict:
    """批量跑 + 汇总成功率。success = 有锚字段中 green 的比例。"""
    fields = fields or FIELDS
    reports = []
    for i, code in enumerate(codes, 1):
        r = run_report(code, year, fields, use_llm)
        reports.append(r)
        log(f"[{i}/{len(codes)}] {code}: " +
            " ".join(f"{fr['field'].split('_')[0]}={fr['outcome']}" for fr in r["fields"]))
    res = _aggregate(reports, fields, year, use_llm)
    res["codes"] = list(codes)
    return res


# ── 实时进度（前端轮询用）──
_PROGRESS_PATH = "goldset/pipeline_progress.json"


def _write_progress(state: Dict) -> None:
    import json
    with open(_PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def load_progress(path: str = _PROGRESS_PATH) -> Optional[Dict]:
    import json
    import os
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_batch_live(codes: List[str], year: int = 2025, fields: List[str] = None,
                   do_scan: bool = True, log=print) -> Dict:
    """边跑边发进度 + 增量存结果：前端可实时看到"正在扫哪家 / i-N / 已完成结局"。"""
    import time
    fields = fields or ["revenue_breakdown", "cost_breakdown", "rnd_info"]
    total = len(codes)
    reports: List[Dict] = []
    state = {"phase": "start", "total": total, "i": 0, "current": None,
             "done": [], "started": time.time(), "updated": time.time(), "codes": list(codes)}
    _write_progress(state)
    for i, code in enumerate(codes, 1):
        state.update(i=i, current=code, phase="scan", updated=time.time())
        _write_progress(state)
        if do_scan:
            inp = _ensure_report_input(code, year)
            if inp.get("reason"):
                log(f"{code} input: {inp['reason']}")
            elif inp.get("downloaded"):
                log(f"{code} PDF downloaded")
        state.update(phase="analyze", updated=time.time())
        _write_progress(state)
        rep = run_report(code, year, fields)
        reports.append(rep)
        for f in fields:                                          # 每份每字段的完整链路写 DB(血缘)
            try:
                save_chain_run(code, year, f)
            except Exception:
                pass
        state["done"].append({"code": code,
                              "outcomes": {fr["field"]: fr["outcome"] for fr in rep["fields"]}})
        state.update(updated=time.time())
        _write_progress(state)
        res = _aggregate(reports, fields, year)                   # 增量汇总 → 存盘，页面网格实时长出来
        res["codes"] = list(codes)
        save_result(res)
        log(f"[{i}/{total}] {code} done")
    state.update(phase="done", current=None, updated=time.time())
    _write_progress(state)
    return load_result()


_ANCHORED_GREEN = ("green", "committed", "verify_hold")


def run_full_pass(year: int = 2025, field: str = "revenue_breakdown",
                  codes: List[str] = None, refresh: bool = False, resume: bool = False,
                  log=print) -> Dict:
    """对 DB 里全部报告跑**完整 LLM 流水线**：绿灯→复核(+选表自愈)，非绿灯→先选表 agent 再诊断。写 DB + 进度。
    refresh=True：每份先 get_tables(refresh=True) 重扫 PDF，清缓存污染(extract_heal 旧 bug 残留)。
    resume=True：接着上次的进度跑，跳过已 done 的(配合墙钟被杀后续跑)。"""
    import time
    from src.eval.test_store import list_latest_runs
    if codes is None:
        codes = sorted({r["stock_code"] for r in list_latest_runs(year, [field]) if r["field"] == field})
    total = len(codes)
    done_codes = set()
    state = {"phase": "verify", "total": total, "i": 0, "current": None,
             "done": [], "started": time.time(), "updated": time.time()}
    if resume:                                    # 接着上次进度跑,跳过已 done 的
        prev = load_progress()
        if prev and prev.get("total") == total:
            state = prev
            state["phase"] = "verify"
            done_codes = {d.get("code") for d in prev.get("done", [])}
    _write_progress(state)
    for i, code in enumerate(codes, 1):
        if code in done_codes:                    # 续跑跳过
            continue
        state.update(i=i, current=code, updated=time.time())
        _write_progress(state)
        if refresh:                               # 清缓存污染:重扫 PDF 拿干净表
            try:
                from src.eval.table_cache import get_tables
                get_tables(code, year, refresh=True)
            except Exception:
                pass
        rec = run_field(code, year, field, use_llm=True)
        try:
            save_chain_run(code, year, field)                 # 先存确定性阶段链路(展示用) → 点详情秒回、不再现场重解
            save_verify_run(code, year, field, rec)           # 再叠复核/自愈结论(沿用刚存的 chain)
        except Exception:
            pass
        state["done"].append({"code": code, "outcome": rec.get("outcome"),
                              "outcomes": {field: rec.get("outcome")},   # 前端 live 视图两种读法都覆盖(单/复数)
                              "healed": rec.get("healed_select"), "verdict": rec.get("verdict")})
        state.update(updated=time.time())
        _write_progress(state)
        tag = " (选表自愈)" if rec.get("healed_select") else ""
        tag += f" [{rec.get('root_cause')}]" if rec.get("llm_kind") == "diagnose" else ""
        log(f"[{i}/{total}] {code} → {rec.get('outcome')}{tag}")
    state.update(phase="done", current=None, updated=time.time())
    _write_progress(state)
    return result_from_db(year, [field])


def run_verify_pass(year: int = 2025, field: str = "revenue_breakdown",
                    target_outcomes=_ANCHORED_GREEN, log=print) -> Dict:
    """对该字段所有"过锚绿灯"(green/committed/verify_hold)逐个跑复核 agent + 选表自愈：
    pass → 入库，wrong_table → 选表自愈重选重解析，hold → 交人工。DB 原生,实时进度。"""
    import time
    from src.eval.test_store import list_latest_runs
    targets = [r for r in list_latest_runs(year, [field])
               if r["field"] == field and r["outcome"] in target_outcomes]
    total = len(targets)
    state = {"phase": "verify", "total": total, "i": 0, "current": None,
             "done": [], "started": time.time(), "updated": time.time()}
    _write_progress(state)
    for i, r in enumerate(targets, 1):
        code = r["stock_code"]
        state.update(i=i, current=code, updated=time.time())
        _write_progress(state)
        rec = run_field(code, year, field, use_llm=True)          # 复核(+选错表则选表自愈)
        try:
            save_verify_run(code, year, field, rec)               # 结论 + 自愈信息写 DB
        except Exception:
            pass
        state["done"].append({"code": code, "verdict": rec.get("verdict"), "outcome": rec.get("outcome"),
                              "healed": rec.get("healed_select")})
        state.update(updated=time.time())
        _write_progress(state)
        log(f"[{i}/{total}] {code} verify={rec.get('verdict')} → {rec.get('outcome')}"
            + (" (选表自愈)" if rec.get("healed_select") else ""))
    state.update(phase="done", current=None, updated=time.time())
    _write_progress(state)
    return result_from_db(year, [field])


# ── DB 血缘(pipeline_runs) —— 存整条链路，读也从 DB ──

def save_chain_run(code: str, year: int, field: str, verify: Dict = None) -> Dict:
    """算一次完整链路并写 DB(append-only)。返回 chain。"""
    from src.eval.test_store import save_run
    ch = field_chain(code, year, field)
    save_run(code, year, field, outcome=ch.get("outcome"), via=ch.get("via"),
             reason=ch.get("reason"), chain=ch, verify=verify)
    return ch


def save_verify_run(code: str, year: int, field: str, rec: Dict) -> None:
    """复核后再写一行：沿用上一条已存的链路，附上复核结论 + 更新 outcome。"""
    from src.eval.test_store import get_latest_run, save_run
    prev = get_latest_run(code, year, field) or {}
    verify = {k: rec.get(k) for k in ("verdict", "summary", "handed_to_human", "suspects",
                                      "heal", "healed_select", "reverify", "reverify_detail", "caliber_gap",
                                      "llm_kind", "decision", "root_cause", "next_action", "evidence", "heal_probe",
                                      "value", "chat", "reverify_chat", "diag_chat", "routed_cat",
                                      "rule_heal", "healed_rule", "extract_heal", "healed_extract",
                                      "codegen", "certified_parser", "cert", "steward",
                                      "reused_parser", "reused_after_hold", "via")   # 各自愈层(含 heal-step-0 复用) → 链上平级显示
              if rec.get(k) is not None}
    save_run(code, year, field, outcome=rec.get("outcome", prev.get("outcome")),
             via=prev.get("via"), reason=prev.get("reason"), chain=prev.get("chain"), verify=verify)


def result_from_db(year: int = 2025, fields: List[str] = None) -> Dict:
    """从 pipeline_runs 取每份最近一次 → 汇成成功率。DB 空则回退 JSON(过渡期)。"""
    from src.eval.test_store import list_latest_runs
    fields = fields or ["revenue_breakdown", "cost_breakdown", "rnd_info"]
    runs = list_latest_runs(year, fields)
    if not runs:
        return load_result() or {"error": "还没有跑批结果"}
    by_code: Dict[str, list] = {}
    for r in runs:
        by_code.setdefault(r["stock_code"], []).append(
            {"field": r["field"], "outcome": r["outcome"], "via": r.get("via"),
             "verify": r.get("verify")})
    reports = [{"code": c, "year": year, "fields": fl} for c, fl in sorted(by_code.items())]
    res = _aggregate(reports, fields, year)
    res["codes"] = [r["code"] for r in reports]
    res["source"] = "db"
    return res


def chain_from_db(code: str, year: int, field: str, recompute: bool = False) -> Dict:
    """读 DB 里存好的链路(秒回)；没有或 recompute=True 则实时算一遍并写 DB。"""
    from src.eval.test_store import get_latest_run
    if not recompute:
        r = get_latest_run(code, year, field)
        if r and r.get("chain"):
            ch = dict(r["chain"])
            ch["outcome"] = r.get("outcome", ch.get("outcome"))    # 行的权威 outcome(含 verify_hold/committed)
            vf = r.get("verify")
            if vf:
                ch["verify_cached"] = vf
                if vf.get("verdict") == "hold" and vf.get("summary"):
                    ch["reason"] = vf["summary"]                    # 复核否决 → 用复核结论当出口原因
            ch["_from_db"] = True
            return ch
    return save_chain_run(code, year, field)


def timeline_from_db(code: str, year: int, field: str, run_id: str = None, limit: int = 200) -> Dict:
    """读取案件时间线；无事件时回退到 chain+verify 的兼容视图。"""
    from src.eval.test_store import list_events
    events = list_events(code, year, field, run_id=run_id, limit=limit)
    if events:
        return {"stock_code": code, "year": year, "field": field,
                "run_id": events[0].get("run_id"), "events": events, "fallback": False}

    ch = chain_from_db(code, year, field, recompute=False) or {}
    out = []
    for st in (ch.get("stages") or []):
        out.append({
            "event_type": st.get("name"),
            "outcome": "ok" if st.get("ok") else "fail",
            "payload": st.get("detail"),
            "created_at": None,
        })
    vf = ch.get("verify_cached") or {}
    if vf:
        out.append({
            "event_type": "verify_cached",
            "outcome": vf.get("verdict"),
            "payload": vf,
            "created_at": None,
        })
    return {"stock_code": code, "year": year, "field": field, "run_id": run_id, "events": out, "fallback": True}


def backfill_from_json(year: int = 2025, fields: List[str] = None, log=print) -> Dict:
    """把当前 pipeline_result.json 里的每份 → 算链路(展示用) + 用 JSON 的 outcome(已含复核结论) + verify → 灌 DB。"""
    from src.eval.test_store import save_run
    fields = fields or ["revenue_breakdown", "cost_breakdown", "rnd_info"]
    res = load_result()
    if not res:
        return {"error": "无 JSON 结果"}
    n = 0
    for rep in res["reports"]:
        code = rep["code"]
        for fo in rep["fields"]:
            if fo["field"] not in fields:
                continue
            ch = field_chain(code, year, fo["field"])
            save_run(code, year, fo["field"],
                     outcome=fo.get("outcome", ch.get("outcome")),   # JSON outcome 权威(含 verify_hold/committed)
                     via=fo.get("via", ch.get("via")), reason=ch.get("reason"),
                     chain=ch, verify=fo.get("verify"))
            n += 1
        log(f"backfill {code} done")
    return {"backfilled": n}
