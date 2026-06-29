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
            "employees": "employee", "top_clients": "supplier", "top_suppliers": "supplier"}
_DBG_ANCHOR = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd_expense"}


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
    return {"code": code, "year": year, "field": field, "sig": sig,
            "anchor": anchor, "total_tables": len(tables), "candidates": cands}


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
