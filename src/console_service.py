"""
控制/审核台后端服务 — 给前端 console 提供真数据

三个真功能：
  · 控制开关 pause/resume/stop（跑批是否继续）
  · 自愈活动记录 /heal/records（按选择即验证路由实跑产出）
  · recode 重过闸：人改解析器代码 → 在缓存表上跑 → 对 golden 打分 → 返回 {score, exact, mismatches}
"""

import base64
import glob
import json
import os
import tempfile
from collections import Counter
from typing import Dict, List, Optional

from src.config import Config

from src.parsers.revenue_router import route_revenue
from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import load_parser
from src.eval.revenue_score import score_revenue
from src.eval.run_eval import load_golden

# ── 控制开关（跑批闸）──
_control = {"running": True}


def control(action: str) -> Dict:
    if action == "pause":
        _control["running"] = False
    elif action == "resume":
        _control["running"] = True
    elif action == "stop":
        _control["running"] = False
        _control["stopped"] = True
    return dict(_control)


def control_state() -> Dict:
    return dict(_control)


# ── 自愈活动记录 ──

def _route_to_record(code: str, year: int) -> Dict:
    r = route_revenue(code, year)
    if r["status"] == "routed":
        return {"stock_code": code, "year": year, "action": "reuse",
                "parser_key": r["parser_key"], "score": 1.0, "rounds": 0,
                "status": "certified"}
    sig = r.get("signal") or {}
    frac = (sig.get("ratio_ok_dims", 0) / sig["n_dims"]) if sig.get("n_dims") else 0.0
    return {"stock_code": code, "year": year, "action": "escalate",
            "parser_key": None, "score": round(frac, 2), "rounds": 0,
            "status": "needs_human"}


def heal_records(codes: Optional[List[str]] = None, year: int = 2025) -> List[Dict]:
    """对一批报告实跑路由，产出自愈活动记录（缓存表，秒级）。默认用 golden 里的报告。"""
    if codes is None:
        codes = [e["stock_code"] for e in load_golden()] or ["000425"]
    out = []
    for c in codes:
        if get_tables(c, year) is None:
            continue
        try:
            out.append(_route_to_record(c, year))
        except Exception as e:
            out.append({"stock_code": c, "year": year, "action": "escalate",
                        "parser_key": None, "score": 0.0, "rounds": 0,
                        "status": "needs_human", "error": str(e)[:80]})
    return out


# ── recode：人改代码 → 重过闸 ──

def _golden_for(code: str, year: int) -> Optional[Dict]:
    for e in load_golden():
        if e["stock_code"] == code and e["year"] == year:
            return e.get("revenue_breakdown")
    return None


def recode(code: str, year: int, new_code: str) -> Dict:
    """人改的解析器代码 → 缓存表上跑 → 对 golden 打分。返回 {score, exact, mismatches, error?}。"""
    tables = get_tables(code, year)
    if tables is None:
        return {"error": "无缓存表"}
    gold = _golden_for(code, year)
    if gold is None:
        return {"error": "该报告无 golden（无法判 exact，请人工核对原文）"}
    tf = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    try:
        tf.write(new_code)
        tf.close()
        rb = load_parser(tf.name)(tables)
        s = score_revenue(rb, gold)
        return {"score": s["score"], "exact": s["exact"],
                "mismatches": [{"dim": m.get("dim"), "name": m.get("name"),
                                "issue": m.get("issue")} for m in s["mismatches"][:10]]}
    except Exception as e:
        return {"error": f"代码运行报错: {str(e)[:200]}"}
    finally:
        os.unlink(tf.name)


# ── 审核任务（结果 + 溯源 + 渲染页 + 解析器源码）──

def _pdf_path(code: str, year: int):
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def _cached_engine_parse(code: str, year: int) -> Optional[Dict]:
    """跑营收解析器拿 结果+溯源，缓存(用缓存表跳过慢抽表)。"""
    cache = os.path.join("goldset", "parse_cache", f"{code}_{year}.json")
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))
    pdf = _pdf_path(code, year)
    if not pdf:
        return None
    # 跑完整引擎拿**全字段**结果(不再只营收)，否则审核页非营收字段全是 None。
    from src.engine_orchestrator import FinParseAI
    rp = FinParseAI().run(pdf, stock_code=code, report_year=year,
                          db_write=False, pre_scan=get_tables(code, year))
    fields = ("revenue_breakdown", "cost_breakdown", "rnd_info",
              "employees", "top_clients", "top_suppliers")
    out = {k: rp.get(k) for k in fields}
    out["溯源"] = rp.get("溯源") or {}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    json.dump(out, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def _render_page_b64(pdf_path: str, page_no: int):
    """渲染 PDF 某页为 base64 PNG，并返回页面点尺寸(与 bbox 同坐标系)。"""
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_no - 1]
    w, h = page.rect.width, page.rect.height
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    b64 = base64.b64encode(pix.tobytes("png")).decode()
    doc.close()
    return f"data:image/png;base64,{b64}", w, h


def _parser_code_for(code: str) -> str:
    """该报告可编辑的解析器源码：优先已有版本文件，否则给个模板。"""
    for pat in (f"rev_{code}_v1.py", f"rev_{code}_*.py"):
        hits = sorted(glob.glob(f"src/parsers/versions/{pat}"))
        if hits:
            return open(hits[0], encoding="utf-8").read()
    return ("def parse(tables, context=None):\n"
            "    # tables: scan_pdf 形状; 返回 {industries/segments/regions: [{name,revenue_yuan,ratio_pct}]}\n"
            "    return {}\n")


def review_task(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    rp = _cached_engine_parse(code, year)
    if rp is None:
        return {"error": "无 PDF/缓存"}
    prov_all = rp.get("溯源") or {}
    # 引擎溯源是 {字段: {路径: {page,bbox}}}。取本字段那块(扁平 路径→{page,bbox})。
    prov_field = prov_all.get(field) if isinstance(prov_all.get(field), dict) else {}
    # 兼容营收老缓存(直接 {路径:{page,bbox}})
    if not prov_field and prov_all and all(isinstance(v, dict) and "page" in v for v in prov_all.values()):
        prov_field = prov_all
    pages = Counter(v["page"] for v in prov_field.values() if isinstance(v, dict) and v.get("page"))
    page = pages.most_common(1)[0][0] if pages else 1
    prov = {k: v for k, v in prov_field.items() if isinstance(v, dict) and v.get("page") == page}
    pdf = _pdf_path(code, year)
    try:
        page_image, w, h = _render_page_b64(pdf, page)
    except Exception:
        page_image, w, h = "", 1, 1
    return {"stock_code": code, "year": year, "page": page, "field": field,
            "page_w_pt": w, "page_h_pt": h, "page_image": page_image,
            "parser_code": _parser_code_for(code),
            "result": rp.get(field), "provenance": prov}


_DBG_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd",
            "employees": "employee", "top_clients": "client", "top_suppliers": "supplier"}
_DBG_ANCHOR = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd_expense"}


def _field_unit_label(code: str, year: int, field: str):
    """该字段所选表的金额单位标签(元/千元/万元/亿元),给 LLM 用。检不到返回 None。
    单位标记('单位：千元')常在表格上方的页面文字里、不在表格单元格中 → 读 PDF 该页文字来检。"""
    try:
        import fitz
        from src.parsers.infra.table_scanner import filter_by_signature
        from src.parsers.infra.unit_detector import detect_unit
        sel = filter_by_signature(get_tables(code, year) or [], _DBG_SIG.get(field, "revenue"))
        pdf = _pdf_path(code, year)
        if not sel or not pdf:
            return None
        page = sel[0].get("page")
        if not page:
            return None
        doc = fitz.open(pdf)
        text = doc[page - 1].get_text() if 0 < page <= len(doc) else ""
        doc.close()
        return {1: "元", 1000: "千元", 10000: "万元", 100000000: "亿元"}.get(detect_unit(text) or 1)
    except Exception:
        return None


def recall_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """选表解耦测试台：① 向量召回 → ② 锚精判 → ③ 维度数闸 → 最终选中。逐候选给三路信号。"""
    from src.parsers.infra.table_recall import (
        vector_recall, anchor_select, _dimension_count, select_table, _table_textdoc, _FIELD_QUERY)
    tables = get_tables(code, year)
    if not tables:
        return {"error": "无缓存（先解析一次该报告）"}
    sig = _DBG_SIG.get(field, "revenue")
    recalled = vector_recall(tables, sig, top_k=8, threshold=0.0)
    judged = anchor_select(recalled, code, year, sig)          # 可能 None(无锚)
    by_page = {id(c.get("table")): c for c in (judged or [])}
    cands = []
    for t in recalled:
        j = by_page.get(id(t.get("table")), {})
        cands.append({"page": t.get("page"), "recall_score": t.get("recall_score"),
                      "anchor_rel": j.get("anchor_rel"), "amount_col": j.get("amount_col"),
                      "dim_count": _dimension_count(t.get("table")) if sig in ("revenue", "cost") else None,
                      "doc": _table_textdoc(t.get("table"))[:90]})
    pick = select_table(tables, code, year, sig)
    return {"code": code, "year": year, "field": field, "sig": sig,
            "query": _FIELD_QUERY.get(sig), "total_tables": len(tables), "has_anchor": judged is not None,
            "candidates": cands,
            "selected": {"page": pick.get("page"), "amount_col": pick.get("amount_col"),
                         "anchor_rel": pick.get("anchor_rel"), "dim_count": pick.get("dim_count"),
                         "via": pick.get("via")} if pick else None}


def select_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """选表调试台：返回该字段所有相关候选表的 得分明细 + 淘汰原因 + 预览，供人工核对选表准不准。"""
    from src.parsers.infra.table_scanner import score_breakdown
    from src.eval.anchors import get_anchors
    sig = _DBG_SIG.get(field, "revenue")
    tables = get_tables(code, year)
    if not tables:
        return {"error": "无 PDF/缓存（先解析一次该报告）", "candidates": []}
    cands = []
    for it in tables:
        bd = score_breakdown(it, sig)
        # 只留"相关"表(入选 / 有caption命中 / 有must_have)，滤掉完全无关的，但保留 near-miss
        relevant = bd["selected"] or any(c["label"] in ("caption命中", "must_have") for c in bd["components"])
        if not relevant:
            continue
        bd["preview"] = [[(c or "").replace("\n", " ").strip()[:14] for c in row]
                         for row in (it["table"] or [])[:40]]   # 预览全表(原10行会看着像不完整)
        bd["table_bbox"] = it.get("table_bbox")     # 给前端在PDF原页上高亮表位置
        cands.append(bd)
    cands.sort(key=lambda x: -x["total"])
    anchor = (get_anchors(code, year) or {}).get(_DBG_ANCHOR.get(field))
    top = cands[0] if cands else None
    try:
        from src.eval.test_store import save_test
        save_test("select", code, year, field,
                  status=("selected" if top and top.get("selected") else "none"),
                  summary={"top_page": top["page"] if top else None,
                           "top_score": top["total"] if top else None,
                           "n_candidates": len(cands), "anchor": anchor},
                  payload={"candidates": [{k: c.get(k) for k in ("page", "total", "selected", "reject", "caption", "section")}
                                          for c in cands]})
    except Exception:
        pass
    return {"code": code, "year": year, "field": field, "sig": sig,
            "anchor": anchor, "total_tables": len(tables), "candidates": cands}


def route_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """路由测试台：这份报告该字段的 指纹 / 命中哪些认证解析器 / 路由到谁 / 试了哪些候选 / 结果过锚没。"""
    from src.eval.field_spec import get_spec
    from src.parsers.revenue_router import route_field, field_plausibility
    from src.eval.anchors import get_anchors
    from src.eval.parser_catalog import load_certified
    if get_tables(code, year) is None:
        return {"error": "无缓存（先解析一次该报告）"}
    spec = get_spec(field)
    r = route_field(spec, code, year)
    sig = field_plausibility(spec, r.get("result"), get_anchors(code, year))
    cands = [c for c in load_certified() if c.get("field", "revenue_breakdown") == field]
    fp = r.get("fingerprint")
    res = r.get("result")
    if isinstance(res, dict):
        summary = {k: len(v) for k, v in res.items() if isinstance(v, list)}
    elif isinstance(res, list):
        summary = {"明细": len(res)}
    else:
        summary = {}
    anchor = (get_anchors(code, year) or {}).get(_DBG_ANCHOR.get(field))
    fp_matched = [c.get("key") for c in cands if fp in (c.get("fingerprints") or [])]
    try:
        from src.eval.test_store import save_test
        save_test("route", code, year, field, status=r["status"], confidence=sig.get("confidence"),
                  summary={"parser_key": r.get("parser_key"), "fp_matched": fp_matched,
                           "tried": len(r.get("tried") or []), "anchored": sig.get("anchored")},
                  payload={"fingerprint": fp, "tried": r.get("tried"), "result_summary": summary})
    except Exception:
        pass
    return {
        "code": code, "year": year, "field": field,
        "fingerprint": fp, "cache_hit": r.get("cache_hit"),
        "status": r["status"], "parser_key": r.get("parser_key"),
        "n_certified_field": len(cands), "certified_keys": [c.get("key") for c in cands],
        "fp_matched": fp_matched,
        "tried": r.get("tried"),
        "confidence": sig.get("confidence"), "anchored": sig.get("anchored"), "anchor": anchor,
        "result_summary": summary, "result": res,
    }


def parse_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """冷启动解析测试台：强制跑通用(冷启动)解析器(绕过路由) → 各维度对锚，看路由未命中时冷启动行不行。"""
    from src.engine_orchestrator import FinParseAI
    from src.eval.field_spec import get_spec
    from src.parsers.revenue_router import field_plausibility
    from src.eval.anchors import get_anchors
    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    if get_tables(code, year) is None:
        return {"error": "无缓存（先解析一次该报告）"}
    spec = get_spec(field)
    parser = FinParseAI()._get_parser(field, pdf)
    try:
        out = parser.parse(pdf, pre_scan=get_tables(code, year))
    except Exception as e:
        return {"error": "冷启动解析异常: " + str(e)[:120], "parser": type(parser).__name__}
    data = out.get(field)
    anchors = get_anchors(code, year)
    sig = field_plausibility(spec, data, anchors)
    anchor = (anchors or {}).get(_DBG_ANCHOR.get(field))
    amt = getattr(spec, "amount_key", "revenue_yuan")
    dims = []
    rows_of = (data.items() if isinstance(data, dict) else [("明细", data)] if isinstance(data, list) else [])
    for k, rows in rows_of:
        if isinstance(rows, list) and rows:
            s = sum((r.get(amt) or 0) for r in rows if isinstance(r, dict))
            dims.append({"dim": k, "n": len(rows), "sum": s,
                         "match": bool(anchor and s and abs(s - anchor) <= 0.03 * anchor)})
    # 溯源(原PDF位置)：解析器直出的 {path:{page,bbox}}；兼容 {field:{path:...}} 嵌套
    prov = out.get("溯源") or {}
    if isinstance(prov.get(field), dict):
        prov = prov[field]
    pages = Counter(v["page"] for v in prov.values() if isinstance(v, dict) and v.get("page"))
    page = pages.most_common(1)[0][0] if pages else None
    try:
        from src.eval.test_store import save_test
        save_test("parse", code, year, field, status=out.get("status"), confidence=sig.get("confidence"),
                  summary={"parser": type(parser).__name__, "dims": {d["dim"]: d["n"] for d in dims},
                           "anchored": sig.get("anchored")}, payload={"dims": dims})
    except Exception:
        pass
    return {"code": code, "year": year, "field": field, "parser": type(parser).__name__,
            "status": out.get("status"), "anchor": anchor,
            "confidence": sig.get("confidence"), "anchored": sig.get("anchored"),
            "dims": dims, "result": data, "amount_key": amt, "page": page, "provenance": prov}


def judge_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """LLM 判定测试台：解析出该字段 → LLM 对照溯源原表逐项判数据对不对(抓锚漏掉的逐行错)。"""
    from src.engine_orchestrator import FinParseAI
    from src.agents.llm_judge import judge_field
    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    if get_tables(code, year) is None:
        return {"error": "无缓存（先解析一次该报告）"}
    parser = FinParseAI()._get_parser(field, pdf)
    try:
        out = parser.parse(pdf, pre_scan=get_tables(code, year))
    except Exception as e:
        return {"error": "解析异常: " + str(e)[:100]}
    value = out.get(field)
    prov = out.get("溯源") or {}
    if isinstance(prov.get(field), dict):
        prov = prov[field]
    try:
        v = judge_field(field, code, year, value, provenance=prov, debug=True,
                        unit_label=_field_unit_label(code, year, field))
    except Exception as e:
        return {"error": "LLM 裁判异常: " + str(e)[:120]}
    try:
        from src.eval.test_store import save_test
        save_test("judge", code, year, field, status=v.get("verdict"), confidence=str(v.get("confidence")),
                  summary={"verdict": v.get("verdict"), "issues": len(v.get("issues") or []), "grounding": v.get("grounding")},
                  payload={"issues": v.get("issues")})
    except Exception:
        pass
    return {"code": code, "year": year, "field": field, "result": value,
            "verdict": v.get("verdict"), "confidence": v.get("confidence"),
            "issues": v.get("issues") or [], "summary": v.get("summary"), "grounding": v.get("grounding"),
            "system": v.get("_system"), "prompt": v.get("_prompt"), "raw": v.get("_raw")}


def heal_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """自愈测试台(真失败筛子)：先判这份要不要自愈——锚/维度一致当裁判,别修没坏的。
    需自愈才给病历+修复方向。无锚/锚过/口径差 一律不自动自愈。"""
    from src.engine_orchestrator import FinParseAI
    from src.eval.field_spec import get_spec
    from src.eval.anchors import get_anchors
    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    if get_tables(code, year) is None:
        return {"error": "无缓存（先解析一次该报告）"}
    spec = get_spec(field)
    parser = FinParseAI()._get_parser(field, pdf)
    try:
        out = parser.parse(pdf, pre_scan=get_tables(code, year))
    except Exception as e:
        return {"error": "解析异常: " + str(e)[:100]}
    data = out.get(field)
    anchor = (get_anchors(code, year) or {}).get(_DBG_ANCHOR.get(field))
    amt = getattr(spec, "amount_key", "revenue_yuan")
    dims = []
    rows_of = (data.items() if isinstance(data, dict) else [("明细", data)] if isinstance(data, list) else [])
    for k, rows in rows_of:
        if isinstance(rows, list) and rows:
            s = sum((r.get(amt) or 0) for r in rows if isinstance(r, dict))
            dims.append({"dim": k, "n": len(rows), "sum": s,
                         "match": bool(anchor and s and abs(s - anchor) <= 0.03 * anchor)})
    dim_sums = [d["sum"] for d in dims if d["sum"]]
    any_match = any(d["match"] for d in dims)
    agree = (max(dim_sums) / min(dim_sums) <= 1.03) if len(dim_sums) >= 2 and min(dim_sums) > 0 else None
    fix_hint = None
    if anchor is None:
        verdict, reason, need = "无锚不自动判", "该字段没有DB锚(如客户/供应商/员工),不在确定性自愈范围,走LLM/人审", False
    elif not dims:
        verdict, reason, need = "需自愈", "解析为空,没抽到分项 → 查选表/认列(表没选对或列没认对)", True
        fix_hint = "上游问题：先看 选表测试 / 认列测试"
    elif any_match:
        verdict, reason, need = "无需自愈", "至少一个维度分项和≈营业收入,数据可信(锚通过)", False
    elif agree is True:
        verdict, reason, need = "疑似口径差(非bug)", "各维度互相一致、只与锚差几个百分点 → 多为'营业收入 vs 营业总收入'口径,交人/LLM判,不自动自愈", False
    else:
        verdict, reason, need = "需自愈", "维度互相矛盾(分项和差异大,疑似抓串/翻倍)", True
        if len(dim_sums) >= 2 and min(dim_sums) > 0 and max(dim_sums) / min(dim_sums) >= 1.8:
            fix_hint = "某维度≈另一维度的2倍 → 疑似切桶漏(dim_leak)，建议规则工具 add_section_marker 补标记"
    return {"code": code, "year": year, "field": field, "anchor": anchor, "dims": dims,
            "any_match": any_match, "dims_agree": agree,
            "verdict": verdict, "reason": reason, "need_heal": need, "fix_hint": fix_hint}


def heal_prepare(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """自愈对话台：拼一个"调试包"给 AI —— 病历 + 失败案例(原表/错值/锚) + 当前配置 + 相关解析器代码,
    让它定位根因、提最小修复(优先改配置/规则)。返回可编辑 messages。不发送。"""
    import inspect
    from src.engine_orchestrator import FinParseAI
    from src.parsers.infra.table_scanner import filter_by_signature
    diag = heal_debug(code, year, field)
    if diag.get("error"):
        return diag
    tables = get_tables(code, year) or []
    sel = filter_by_signature(tables, _DBG_SIG.get(field, "revenue"))
    table = (sel[0].get("table") if sel else []) or []
    table_text = "\n".join(" | ".join((c or "") for c in row) for row in table[:30])
    pdf = _pdf_path(code, year)
    parser = FinParseAI()._get_parser(field, pdf)
    try:
        value = parser.parse(pdf, pre_scan=tables).get(field)
    except Exception:
        value = None
    try:
        config = open("src/parser_rules/revenue.yaml", encoding="utf-8").read()
    except Exception:
        config = "(读不到 revenue.yaml)"
    code_src = ""
    for m in ("_detect_columns", "_resolve_columns", "_classify"):
        fn = getattr(parser, m, None)
        if fn:
            try:
                code_src += inspect.getsource(fn) + "\n"
            except Exception:
                pass
    dims_txt = "  ".join(f"{d['dim']}={d['sum']/1e8:.0f}亿({'过锚' if d['match'] else '✗'})" for d in diag.get("dims", []))
    anchor = diag.get("anchor")
    sys_msg = ("你是资深的 A 股财报解析器开发者。下面给你：病历 + 失败案例(原表/解析出的错值/锚) + 当前配置 + 相关解析器代码。"
               "请定位 bug 的根因(具体到哪条配置或哪行代码)，并提出**最小修复**。"
               "优先回答：能不能只加/改一条配置或规则解决？给出具体改动。实在不行才改代码。")
    user = (
        f"## 病历（确定性诊断）\n判定：{diag.get('verdict')}；理由：{diag.get('reason')}\n"
        f"各维度分项和：{dims_txt}\n锚(营业收入)：{anchor/1e8:.2f}亿\n\n"
        f"## 失败案例\n字段：{field}\n选中的原表（前30行）：\n{table_text}\n\n"
        f"解析出的值：\n{json.dumps(value, ensure_ascii=False, indent=2)[:2500]}\n\n"
        f"## 当前配置 src/parser_rules/revenue.yaml\n{config}\n\n"
        f"## 相关解析器代码（认列/切桶）\n```python\n{code_src[:4000]}\n```\n\n"
        f"## 任务\n1) bug 根因在哪（指到具体配置项/代码行）？\n"
        f"2) 最小修复是什么（优先：加/改哪条配置或规则）？\n"
        f"3) 如果修复就是'给 dimensions 加一个切桶标记'，**最后**用一个 json 代码块给出可执行修复：\n"
        f'```json\n{{"tool": "add_section_marker", "text": "报告里没被识别的表头写法", "dim": "industries|segments|regions|by_channel"}}\n```\n'
        f'如果不是这类规则修复（要改代码/别的），给 {{"tool": "none"}}。'
    )
    return {"code": code, "year": year, "field": field, "diag": diag,
            "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]}


def _extract_fix(reply: str):
    """从 AI 回复里抠出结构化修复 {tool,...}（json 代码块优先）。none/抠不到→返回 None。"""
    import re
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL)
    blocks += re.findall(r'(\{[^{}]*"tool"[^{}]*\})', reply, re.DOTALL)
    for b in blocks:
        try:
            d = json.loads(b)
        except Exception:
            continue
        if isinstance(d, dict) and d.get("tool") and d.get("tool") != "none":
            return d
    return None


def heal_chat(code: str, year: int, field: str, messages: list) -> Dict:
    """自愈对话：把(可编辑过的) messages 发给 AI,记录,返回修复建议 + 解析出的结构化修复 fix。"""
    from src.agents.llm_client import chat
    try:
        reply = chat(messages, role="judge", temperature=0.3)
    except Exception as e:
        return {"error": "LLM 调用异常: " + str(e)[:120]}
    try:
        from src.eval.test_store import save_chat
        save_chat(code, year, field + "|heal", messages, reply)
    except Exception:
        pass
    return {"reply": reply, "fix": _extract_fix(reply)}


def apply_fix(code: str, year: int, field: str, fix: dict) -> Dict:
    """应用 AI 给的结构化修复(目前支持 add_section_marker) → 回链重测,返回 修复前后对照。"""
    from src.agents.rule_tools import add_section_marker
    tool = (fix or {}).get("tool")
    before = heal_debug(code, year, field)
    if tool == "add_section_marker":
        r = add_section_marker(fix.get("text"), fix.get("dim"), field="revenue")
    else:
        return {"ok": False, "message": f"暂不支持的工具：{tool}（本期只接了 add_section_marker）"}
    after = heal_debug(code, year, field)
    fixed = bool(r.get("ok") and before.get("need_heal") and not after.get("need_heal"))
    return {"ok": bool(r.get("ok")), "apply": r,
            "before": {"verdict": before.get("verdict"), "need_heal": before.get("need_heal"), "dims": before.get("dims")},
            "after": {"verdict": after.get("verdict"), "need_heal": after.get("need_heal"), "dims": after.get("dims")},
            "fixed": fixed}


def columns_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """认列测试台：选中表 → 解析器怎么判 名称列/金额列/占比列(内容法 + 表头法 + 最终)。"""
    from src.engine_orchestrator import FinParseAI
    from src.parsers.infra.table_scanner import filter_by_signature
    tables = get_tables(code, year)
    if not tables:
        return {"error": "无缓存（先解析一次该报告）"}
    sel = filter_by_signature(tables, _DBG_SIG.get(field, "revenue"))
    if not sel:
        return {"error": "没选到目标表"}
    table = sel[0].get("table") or []
    pdf = _pdf_path(code, year)
    p = FinParseAI()._get_parser(field, pdf)
    n_cols = max((len(r) for r in table), default=0)
    out = {"code": code, "year": year, "field": field,
           "page": sel[0].get("page"),
           "table": [[(c or "") for c in row] for row in table[:30]],
           "n_cols": n_cols}
    # 逐列数格子(认列的原始依据)：每列有几个 文字/数字/百分比 单元格
    try:
        stats = []
        for ci in range(n_cols):
            t = num = r = 0
            for row in table:
                if ci < len(row) and row[ci]:
                    cv = row[ci].replace("\n", " ").strip()
                    if not cv:
                        continue
                    if "%" in cv:
                        r += 1
                    elif p._looks_like_money(cv):
                        num += 1
                    elif p._is_text(cv):
                        t += 1
            stats.append({"col": ci, "text": t, "number": num, "ratio": r})
        out["col_stats"] = stats
    except Exception:
        pass
    out["steps"] = [
        "第1步 逐列数格子：每列数清有几个 文字 / 像钱的数字 / 百分比 单元格（见下表）",
        "第2步 找占比列：哪列有 ≥3 个 0~100% 的百分比 → 占比列（表头没命中占比别名时才认）",
        "第3步 找金额列：哪列有 ≥3 个 像钱的数 → 金额列；若和占比列撞了，另选一个",
        "第4步 找名称列：排除金额/占比列后，第一个有 ≥3 个文字的列 → 名称列",
        "第5步 表头法覆盖：若 revenue.yaml 有表头别名，按表头精确认列，覆盖上面的统计结果",
    ]
    try:
        if hasattr(p, "_detect_columns"):
            cn, ca, cr = p._detect_columns(table)
            out["content_method"] = {"name": cn, "amount": ca, "ratio": cr}
        if hasattr(p, "_resolve_columns"):
            aliases = p._header_aliases() if hasattr(p, "_header_aliases") else None
            out["has_yaml_rule"] = bool(aliases)
            if aliases:
                from src.parsers.infra.header_columns import detect_columns_by_header
                hdr = detect_columns_by_header(table, aliases)
                out["header_method"] = {"name": hdr.get("name"), "amount": hdr.get("revenue"), "ratio": hdr.get("ratio")}
            fn, fa, fr = p._resolve_columns(table)
            out["final"] = {"name": fn, "amount": fa, "ratio": fr}
    except Exception as e:
        out["warn"] = "认列异常: " + str(e)[:100]
    # 这份财报的**具体**认列过程(带真实列号/候选/原因),不是通用步骤
    try:
        stats = {s["col"]: s for s in out.get("col_stats", [])}
        pct_cand, money_cand = [], []
        for ci in range(n_cols):
            valid = 0
            for row in table:
                if ci < len(row) and row[ci] and "%" in row[ci]:
                    try:
                        x = float(row[ci].replace("%", "").replace(",", "").strip())
                        if 0 <= x <= 100:
                            valid += 1
                    except ValueError:
                        pass
            if valid >= 3:
                pct_cand.append(ci)
            if stats.get(ci, {}).get("number", 0) >= 3:
                money_cand.append(ci)
        cm = out.get("content_method", {})
        cn, ca, cr = cm.get("name"), cm.get("amount"), cm.get("ratio")
        fin = out.get("final", {})
        hm = out.get("header_method")
        trace = [f"① 数格子：这张表共 {n_cols} 列、{len(table)} 行（逐列统计见下表）"]
        trace.append(
            f"② 找占比列：列 {pct_cand} 各有 ≥3 个 0~100% 的百分比 → 取第一个 = 列{cr}" if pct_cand
            else "② 找占比列：没有任何列有 ≥3 个带 % 的 0~100 数 → 占比列空（多半是占比没带 % 号，如写成 28.21）")
        trace.append(
            f"③ 找金额列：列 {money_cand} 各有 ≥3 个像钱的数 → 取第一个 = 列{ca}" if money_cand
            else "③ 找金额列：没有列满足像钱的数 ≥3 → 金额列空")
        trace.append(
            f"④ 找名称列：排除金额列{ca}、占比列{cr} 后，列{cn} 有 {stats.get(cn, {}).get('text', 0)} 个文字 → 名称列 = 列{cn}"
            if cn is not None else "④ 找名称列：没找到文字列")
        if out.get("has_yaml_rule") and hm:
            trace.append(f"⑤ 表头法(读 revenue.yaml 别名)：按表头认出 名称={hm.get('name')} 金额={hm.get('amount')} 占比={hm.get('ratio')} → 覆盖统计法")
        else:
            trace.append("⑤ 表头法：没有 YAML 别名 → 直接用统计法结果")
        trace.append(f"✅ 最终：名称=列{fin.get('name')}、金额=列{fin.get('amount')}、占比=列{fin.get('ratio')}")
        out["trace"] = trace
    except Exception:
        pass
    return out


def judge_prepare(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """对话台：解析该字段 → 拼好发给 LLM 的 messages(system+user) 但**不发送**,返给前端编辑。"""
    from src.engine_orchestrator import FinParseAI
    from src.agents.llm_judge import build_judge_messages
    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    if get_tables(code, year) is None:
        return {"error": "无缓存（先解析一次该报告）"}
    parser = FinParseAI()._get_parser(field, pdf)
    try:
        out = parser.parse(pdf, pre_scan=get_tables(code, year))
    except Exception as e:
        return {"error": "解析异常: " + str(e)[:100]}
    value = out.get(field)
    prov = out.get("溯源") or {}
    if isinstance(prov.get(field), dict):
        prov = prov[field]
    unit_label = _field_unit_label(code, year, field)
    messages, grounding = build_judge_messages(field, code, year, value, provenance=prov, unit_label=unit_label)
    if messages is None:
        return {"error": "无源文(溯源+RAG都没有),无法对话", "grounding": grounding}
    return {"code": code, "year": year, "field": field, "grounding": grounding,
            "unit": unit_label, "messages": messages, "result": value}


def judge_chat(code: str, year: int, field: str, messages: list) -> Dict:
    """把(可能被人编辑过的) messages 发给 LLM,记录整段对话,返回回复 + 解析出的判定。"""
    from src.agents.llm_client import chat
    from src.agents.llm_judge import _extract_json
    try:
        reply = chat(messages, role="judge", temperature=0.3)
    except Exception as e:
        return {"error": "LLM 调用异常: " + str(e)[:120]}
    # 解析回复 → 判"是否完全正确"：verdict=ok 且无任何 issue
    v = _extract_json(reply) or {}
    verdict, conf = v.get("verdict"), v.get("confidence")
    issues = v.get("issues") or []
    all_ok = (verdict == "ok" and len(issues) == 0) if verdict in ("ok", "suspicious") else None
    try:
        from src.eval.test_store import save_chat
        save_chat(code, year, field, messages, reply)
    except Exception:
        pass
    # LLM 判完全正确 → 重解析拿结果,送入库审核队列(pending,等人通过)
    commit_id = None
    if all_ok:
        try:
            from src.engine_orchestrator import FinParseAI
            from src.eval.test_store import enqueue_commit
            pdf = _pdf_path(code, year)
            result = FinParseAI()._get_parser(field, pdf).parse(pdf, pre_scan=get_tables(code, year)).get(field)
            commit_id = enqueue_commit(code, year, field, result, conf)
        except Exception:
            pass
    return {"reply": reply, "verdict": verdict, "confidence": conf,
            "issues": issues, "all_ok": all_ok, "commit_id": commit_id}


_COMMIT_COLUMNS = {"revenue_breakdown", "cost_breakdown", "top_clients",
                   "top_suppliers", "employees", "rnd_info"}   # financial_reports 里对应的列


def commit_approve(rid: int, note: str = "") -> Dict:
    """人审通过 → 把解析结果写进生产库 financial_reports.{field}(年报行)。"""
    from src.eval.test_store import get_commit, set_commit_status
    rec = get_commit(rid)
    if not rec:
        return {"error": "记录不存在"}
    if rec.get("status") != "pending":
        return {"error": "该记录已处理(" + str(rec.get("status")) + ")"}
    field = rec["field"]
    if field not in _COMMIT_COLUMNS:
        return {"error": "字段 " + field + " 不支持入库"}
    try:
        from src.database import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # field 已过白名单,可安全拼列名
                cur.execute(
                    f"UPDATE financial_reports SET {field}=%s, pdf_parsed_at=NOW() "
                    "WHERE stock_code=%s AND report_year=%s AND report_quarter='annual'",
                    (rec["result_json"], rec["stock_code"], rec["year"]))
                n = cur.rowcount
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return {"error": "入库失败: " + str(e)[:140]}
    set_commit_status(rid, "approved", note)
    return {"ok": True, "rows_updated": n, "field": field, "code": rec["stock_code"]}


def commit_reject(rid: int, note: str = "") -> Dict:
    """人审驳回 → 不入库。"""
    from src.eval.test_store import get_commit, set_commit_status
    rec = get_commit(rid)
    if not rec:
        return {"error": "记录不存在"}
    set_commit_status(rid, "rejected", note)
    return {"ok": True}


def render_page(code: str, year: int, page: int) -> Dict:
    """渲染某报告某页为 base64 PNG（选表调试台"看PDF原页"用）。页码越界返回 error。"""
    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    try:
        import fitz
        doc = fitz.open(pdf)
        n = len(doc)
        doc.close()
        if page < 1 or page > n:
            return {"error": "页码越界", "page": page}
        img, w, h = _render_page_b64(pdf, page)
        return {"page": page, "page_image": img, "page_w_pt": w, "page_h_pt": h}
    except Exception as e:
        return {"error": str(e)[:100]}


# ── 人在回路写回：确认真值→golden，认证解析器→目录 ──

_GOLDEN = "goldset/revenue_golden.json"


def save_golden(code: str, year: int, revenue_breakdown: Dict,
                status: str = "confirmed_by_human", note: str = "") -> Dict:
    """人确认的营收结果 → 存为该报告的 golden（合并,保留其它条目）。"""
    d = json.load(open(_GOLDEN, encoding="utf-8")) if os.path.exists(_GOLDEN) else {"entries": []}
    entries = [e for e in d.get("entries", []) if not (e["stock_code"] == code and e["year"] == year)]
    entries.append({"stock_code": code, "year": year, "_status": status,
                    "_note": note, "revenue_breakdown": revenue_breakdown})
    d["entries"] = sorted(entries, key=lambda e: (e["stock_code"], e["year"]))
    os.makedirs(os.path.dirname(_GOLDEN) or ".", exist_ok=True)
    json.dump(d, open(_GOLDEN, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return {"saved": True, "stock_code": code, "year": year, "status": status}


def certify_parser(code: str, year: int, code_src: str, key: str = None) -> Dict:
    """认证一个解析器：服务端重验对 golden 必须 exact → 写版本文件 → 登记入目录。"""
    res = recode(code, year, code_src)          # 重过闸（不信前端的 exact 声明）
    if "error" in res:
        return res
    if not res.get("exact"):
        return {"error": "未到 exact，不能认证（正确率优先）",
                "score": res.get("score"), "mismatches": res.get("mismatches")}
    from src.eval.parser_catalog import certify
    from src.eval.route_index import fingerprint_of
    path = f"src/parsers/versions/rev_{code}_{year}.py"
    with open(path, "w", encoding="utf-8") as f:
        f.write(code_src)
    key = key or f"{code}-{year}-人工认证"
    fp = fingerprint_of(code, year)             # 记录版式指纹 → 缩候选索引
    certify(key, path, fingerprints=[fp] if fp else None)
    return {"certified": True, "parser_key": key, "path": path, "score": 1.0, "fingerprint": fp}
