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
    # 缓存优先，未命中则按需下载（复用 book-agent 巨潮方案）
    from src.parsers.infra.pdf_locator import ensure_pdf
    return ensure_pdf(code, year)


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
# judge_diagnose 的这些根因属"选表/抽表"层面，暂无自愈能力 → 一律交人工(进分诊队列 needs_human)。
_NO_AUTOHEAL_ROOT = {"incomplete_table", "wrong_table"}


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
                      "caption": (t.get("caption") or "").strip(),   # 表上文标题(已并入召回文档)
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
    # 溯源：routed 版本解析器多不产 cell bbox → 退而给该字段所在表的 PDF 页(select_table 选中页)供对照
    prov = res.get("溯源") if isinstance(res, dict) else None
    page = None
    try:
        from src.parsers.infra.table_recall import select_table
        sel = select_table(get_tables(code, year), code, year, _DBG_SIG.get(field, "revenue"))
        page = (sel or {}).get("page")
    except Exception:
        pass
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
        "page": page, "provenance": prov or {}, "amount_key": getattr(spec, "amount_key", "revenue_yuan"),
    }


def parse_debug(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """解析测试台（路由优先，和生产 orchestrator 一致）：先②路由(选择即验证)——**命中认证解析器就用它、
    不再冷启动**；没命中才回退冷启动通用解析器。各维度对锚看解得对不对。"""
    from src.engine_orchestrator import FinParseAI
    from src.eval.field_spec import get_spec
    from src.parsers.revenue_router import field_plausibility, route_field
    from src.eval.anchors import get_anchors
    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    if get_tables(code, year) is None:
        return {"error": "无缓存（先解析一次该报告）"}
    spec = get_spec(field)
    # 第2步:先路由。命中→用路由结果、**不冷启动**;没命中→冷启动(与生产 orchestrator._route_field 一致)
    rt = route_field(spec, code, year)
    routed = rt.get("status") == "routed"
    if routed:
        res = rt.get("result")
        data = res.get(field) if isinstance(res, dict) and field in res else res
        prov = (res.get("溯源") if isinstance(res, dict) else None) or {}
        pstatus, parser_name, source = "ok", rt.get("parser_key"), "routed"
    else:
        parser = FinParseAI()._get_parser(field, pdf)
        try:
            out = parser.parse(pdf, pre_scan=get_tables(code, year), code=code, year=year)
        except Exception as e:
            return {"error": "冷启动解析异常: " + str(e)[:120], "parser": type(parser).__name__}
        data = out.get(field)
        prov = out.get("溯源") or {}
        if isinstance(prov.get(field), dict):
            prov = prov[field]
        pstatus, parser_name, source = out.get("status"), type(parser).__name__, "cold_start"
    anchors = get_anchors(code, year)
    try:
        sig = field_plausibility(spec, data, anchors)
    except Exception:
        sig = {}
    anchor = (anchors or {}).get(_DBG_ANCHOR.get(field))
    amt = getattr(spec, "amount_key", "revenue_yuan")
    dims = []
    rows_of = (data.items() if isinstance(data, dict) else [("明细", data)] if isinstance(data, list) else [])
    for k, rows in rows_of:
        if isinstance(rows, list) and rows:
            s = sum((r.get(amt) or 0) for r in rows if isinstance(r, dict))
            dims.append({"dim": k, "n": len(rows), "sum": s,
                         "match": bool(anchor and s and abs(s - anchor) <= 0.03 * anchor)})
    # 溯源(原PDF位置)：解析器直出的 {path:{page,bbox}}（prov 上面已按 routed/冷启动取好）
    pages = Counter(v["page"] for v in prov.values() if isinstance(v, dict) and v.get("page"))
    page = pages.most_common(1)[0][0] if pages else None
    try:
        from src.eval.test_store import save_test
        save_test("parse", code, year, field, status=pstatus, confidence=sig.get("confidence"),
                  summary={"parser": parser_name, "source": source, "dims": {d["dim"]: d["n"] for d in dims},
                           "anchored": sig.get("anchored")}, payload={"dims": dims})
    except Exception:
        pass
    return {"code": code, "year": year, "field": field, "parser": parser_name,
            "source": source, "routed": routed, "parser_key": rt.get("parser_key"),
            "status": pstatus, "anchor": anchor,
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
        out = parser.parse(pdf, pre_scan=get_tables(code, year), code=code, year=year)
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
        out = parser.parse(pdf, pre_scan=get_tables(code, year), code=code, year=year)
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
    """自愈对话台：拼诊断调试包（Prompt Registry → diagnose agent）。返回可编辑 messages，不发送。"""
    from src.agents.diagnose_agent import prepare_diagnose
    return prepare_diagnose(code, year, field)


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
    from src.agents.llm_routing import resolve_model
    try:
        reply = chat(messages, role="judge", temperature=0.3, model=resolve_model("diagnose"))
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
    from src.parsers.infra.table_recall import select_table
    tables = get_tables(code, year)
    if not tables:
        return {"error": "无缓存（先解析一次该报告）"}
    # 用选表解耦的同一套 select_table(召回+锚+维度) 选表,和"选表解耦/解析"台一致(不再用旧 filter_by_signature)
    sel = select_table(tables, code, year, _DBG_SIG.get(field, "revenue"))
    if not sel or not sel.get("table"):
        return {"error": "没选到目标表"}
    table = sel.get("table") or []
    pdf = _pdf_path(code, year)
    p = FinParseAI()._get_parser(field, pdf)
    n_cols = max((len(r) for r in table), default=0)
    out = {"code": code, "year": year, "field": field,
           "page": sel.get("page"),
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
    """对话台：judge_diagnose 分层诊断调试包（Prompt Registry）。"""
    from src.agents.judge_diagnose_agent import prepare_judge_diagnose

    return prepare_judge_diagnose(code, year, field)


def rule_code_prepare(code: str, year: int, field: str = "revenue_breakdown", stage1: Dict = None) -> Dict:
    """对话台：rule_code_diagnose 第二阶段调试包（规则/代码层）。"""
    from src.agents.rule_code_diagnose_agent import prepare_rule_code_diagnose

    return prepare_rule_code_diagnose(code, year, field, stage1=stage1 or {})


def judge_chat(code: str, year: int, field: str, messages: list) -> Dict:
    """把(可能被人编辑过的) messages 发给 LLM,记录整段对话,返回回复 + 解析出的判定。"""
    from src.agents.llm_client import chat
    from src.agents.llm_judge import _extract_json
    from src.agents.llm_routing import resolve_model
    try:
        reply = chat(messages, role="judge", temperature=0.3, model=resolve_model("judge"))
    except Exception as e:
        return {"error": "LLM 调用异常: " + str(e)[:120]}
    # 解析回复：优先读新契约 decision/root_cause；旧契约 verdict/issues 继续兼容
    v = _extract_json(reply) or {}
    decision = v.get("decision")
    root_cause = v.get("root_cause")
    next_action = v.get("next_action")
    evidence = v.get("evidence") or []
    if isinstance(evidence, str):                 # LLM 常把 evidence 写成一句话而非数组 → 统一成 list
        evidence = [evidence]
    elif not isinstance(evidence, list):
        evidence = [str(evidence)]
    summary = v.get("summary")
    verdict, conf = v.get("verdict"), v.get("confidence")
    issues = v.get("issues") or []
    if decision:
        all_ok = True if decision == "ok" else False
    else:
        all_ok = (verdict == "ok" and len(issues) == 0) if verdict in ("ok", "suspicious") else None
    # 选表/跨页类根因目前无自愈能力 → 确定性地改判交人工（不依赖 LLM 说什么 next_action），并进分诊队列。
    handed_to_human = False
    if root_cause in _NO_AUTOHEAL_ROOT:
        next_action = "handoff_human"
        try:
            from src.eval.triage_queue import enqueue
            note = summary or (evidence[0] if evidence else "") or root_cause or ""
            enqueue(code, year, field, "needs_human", note=f"{root_cause}: {note}"[:200])
            handed_to_human = True
        except Exception:
            pass
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
            result = FinParseAI()._get_parser(field, pdf).parse(pdf, pre_scan=get_tables(code, year), code=code, year=year).get(field)
            commit_id = enqueue_commit(code, year, field, result, conf)
        except Exception:
            pass
    return {
        "reply": reply,
        "decision": decision,
        "root_cause": root_cause,
        "next_action": next_action,
        "summary": summary,
        "evidence": evidence,
        "handed_to_human": handed_to_human,
        # legacy fields
        "verdict": verdict,
        "confidence": conf,
        "issues": issues,
        "all_ok": all_ok,
        "commit_id": commit_id,
    }


def verify_prepare(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """复核对话台（绿灯专用）：解析 → 算锚信号 → 拼好发给**复核 agent** 的 messages(不发送)，返前端编辑。
    复核 agent 是给'锚已过的绿灯'审盲区用的；非绿灯会附提示但仍允许看 prompt。"""
    from src.engine_orchestrator import FinParseAI
    from src.agents.llm_judge import build_verify_messages
    from src.parsers.revenue_router import field_plausibility
    from src.eval.anchors import get_anchors
    from src.eval.field_spec import get_spec
    pdf = _pdf_path(code, year)
    if not pdf:
        return {"error": "无 PDF"}
    if get_tables(code, year) is None:
        return {"error": "无缓存（先解析一次该报告）"}
    parser = FinParseAI()._get_parser(field, pdf)
    try:
        out = parser.parse(pdf, pre_scan=get_tables(code, year), code=code, year=year)
    except Exception as e:
        return {"error": "解析异常: " + str(e)[:100]}
    value = out.get(field)
    prov = out.get("溯源") or {}
    if isinstance(prov.get(field), dict):
        prov = prov[field]
    try:
        sig = field_plausibility(get_spec(field), value, get_anchors(code, year))
    except Exception:
        sig = {}
    conf = sig.get("confidence")
    note = "" if conf == "high" else f"注意：此字段当前锚置信={conf}，不是绿灯(锚过)。复核 agent 本是给绿灯审盲区用的。"
    unit_label = _field_unit_label(code, year, field)
    messages, grounding = build_verify_messages(field, code, year, value, sig, provenance=prov, unit_label=unit_label)
    if messages is None:
        return {"error": "无源文(溯源+RAG都没有),无法复核", "grounding": grounding}
    return {"code": code, "year": year, "field": field, "grounding": grounding,
            "unit": unit_label, "messages": messages, "result": value,
            "confidence": conf, "note": note}


def verify_chat(code: str, year: int, field: str, messages: list) -> Dict:
    """把(可编辑过的)复核 messages 发给复核 agent，记录，返回回复 + 解析出的 pass/hold 裁决。"""
    from src.agents.llm_client import chat
    from src.agents.llm_judge import _extract_json
    from src.agents.llm_routing import resolve_model
    try:
        reply = chat(messages, role="judge", temperature=0.1, model=resolve_model("verify"))
    except Exception as e:
        return {"error": "LLM 调用异常: " + str(e)[:120]}
    v = _extract_json(reply) or {}
    verdict = v.get("verdict")
    suspects = v.get("suspects") or v.get("issues") or []
    passed = True if verdict == "pass" else (False if verdict == "hold" else None)
    try:
        from src.eval.test_store import save_chat
        save_chat(code, year, f"{field}::verify", messages, reply)   # 与 judge 历史分开
    except Exception:
        pass
    # 复核 hold = 体检不过(选错表/跨页)或有项对不上 → 交人工，不入库
    handed_to_human = False
    if verdict == "hold":
        try:
            from src.eval.triage_queue import enqueue
            issues = ", ".join(f"{s.get('issue')}" for s in suspects if s.get("issue"))[:120]
            enqueue(code, year, field, "needs_human", note=f"verify hold: {issues or v.get('summary','')}"[:200])
            handed_to_human = True
        except Exception:
            pass
    # 复核 pass = LLM 逐项对照源文点头 = 终审通过 → 自动入库(测试库,留痕 source=verify_agent)
    committed = None
    if verdict == "pass" and field in _COMMIT_COLUMNS:
        try:
            from src.engine_orchestrator import FinParseAI
            from src.eval.triage_queue import _auto_commit
            pdf = _pdf_path(code, year)
            result = FinParseAI()._get_parser(field, pdf).parse(
                pdf, pre_scan=get_tables(code, year), code=code, year=year).get(field)
            committed = _auto_commit(code, year, field, result, {"confidence": "high"})
        except Exception as e:
            committed = f"error:{str(e)[:60]}"
    return {"reply": reply, "verdict": verdict, "suspects": suspects,
            "passed": passed, "summary": v.get("summary"), "committed": committed,
            "handed_to_human": handed_to_human}


_COMMIT_COLUMNS = {"revenue_breakdown", "cost_breakdown", "top_clients",
                   "top_suppliers", "employees", "rnd_info"}   # financial_reports 里对应的列


def commit_approve(rid: int, note: str = "") -> Dict:
    """人审通过 → 把解析结果写进报告表 {REPORTS_TABLE}.{field}(年报行)。
    默认生产 financial_reports；测试时 REPORTS_TABLE=financial_reports_test 则打到镜像表。"""
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
        from src.database import get_conn, reports_table
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # field 已过白名单,可安全拼列名；表名走 reports_table 开关(测试打镜像表)
                cur.execute(
                    f"UPDATE `{reports_table()}` SET {field}=%s, pdf_parsed_at=NOW() "
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
    # 认证后旧值作废：引擎缓存 + 单一真源都要重算，否则审核台/裁判仍显示旧解析结果
    _pc = os.path.join("goldset", "parse_cache", f"{code}_{year}.json")
    if os.path.exists(_pc):
        os.remove(_pc)
    from src.eval.canonical import invalidate as _inv_canonical
    _inv_canonical(code, year)
    return {"certified": True, "parser_key": key, "path": path, "score": 1.0, "fingerprint": fp}


# ── 批量预下载 PDF（下载表：把"下载"从"测试"里剥离，先下好再纯解析测）──

def _has_cache(code: str, year: int) -> bool:
    return bool(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))


def download_list(board: str = "star", year: int = 2025) -> dict:
    """某范围内"有 pdf_source_url(可下载)"的 code + 已缓存标注。board=star(科创板688/689)/all。"""
    from src.database import get_conn
    like = {"star": ("688%", "689%")}.get(board)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            base = ("SELECT DISTINCT stock_code FROM financial_reports "
                    "WHERE report_quarter='annual' AND report_year=%s "
                    "AND pdf_source_url IS NOT NULL AND pdf_source_url<>''")
            if like:
                cur.execute(base + " AND (stock_code LIKE %s OR stock_code LIKE %s)", (year, like[0], like[1]))
            else:
                cur.execute(base, (year,))
            codes = sorted(r["stock_code"] for r in cur.fetchall())
    finally:
        conn.close()
    cached = [c for c in codes if _has_cache(c, year)]
    return {"board": board, "year": year, "codes": codes, "total": len(codes),
            "cached_count": len(cached), "pending": len(codes) - len(cached)}


def download_batch(codes: list, year: int = 2025) -> dict:
    """批量下载一批 code 的 PDF(ensure_pdf)。前端分块调用。逐个返回:
    status ∈ cached(本已有) | downloaded(新下) | failed(无url/下载失败)。"""
    from src.parsers.infra.pdf_locator import ensure_pdf
    out = []
    for code in (codes or []):
        try:
            had = _has_cache(code, year)
            path = ensure_pdf(code, year, download=True)
            out.append({"code": code, "status": ("cached" if had else "downloaded") if path else "failed"})
        except Exception as e:
            out.append({"code": code, "status": "failed", "err": str(e)[:80]})
    return {"results": out}
