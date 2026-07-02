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


def _pdf(code: str, year: int) -> Optional[str]:
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


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


def run_field(code: str, year: int, field: str, anchors: Dict = None,
              tables=None, pdf: str = None, use_llm: bool = False) -> Dict:
    """跑一个字段到出口。确定性部分不发 LLM；use_llm=True 才补 verify/judge_diagnose。
    outcome ∈ green | non_green | no_anchor | no_data | no_input
             （use_llm 时 green 细化为 committed/verify_hold，non_green 细化为 diagnosis 结论）。"""
    spec = get_spec(field)
    tables = tables if tables is not None else get_tables(code, year)
    pdf = pdf or _pdf(code, year)
    if anchors is None:                                       # 单跑(endpoint)时自动取锚
        try:
            from src.eval.anchors import get_anchors
            anchors = get_anchors(code, year) or {}
        except Exception:
            anchors = {}
    if not tables or not pdf:
        return {"field": field, "outcome": "no_input"}

    # ① 生产路由优先：命中认证解析器且过硬规则 = 直接绿灯（真实链路的第一步）
    try:
        from src.parsers.revenue_router import route_field
        rt = route_field(spec, code, year)
    except Exception:
        rt = {"status": "error"}
    if rt.get("status") == "routed":
        rec = {"field": field, "via": "routed", "outcome": "green",
               "confidence": (rt.get("signal") or {}).get("confidence")}
        if use_llm:
            rec.update(_green_llm(code, year, field, rt.get("result"), rt.get("signal") or {}, spec))
        return rec

    # ② 冷启动兜底：无认证解析器时引擎默认解析器 + 锚判
    value = _cold_parse(code, year, field, tables, pdf)
    if not value:
        return {"field": field, "outcome": "no_data", "via": "cold"}   # 解析器没解出东西 → needs_write
    sig = field_plausibility(spec, value, anchors or {})
    conf = sig.get("confidence")
    rec = {"field": field, "via": "cold", "confidence": conf, "anchored": sig.get("anchored")}

    if conf == "high":                                          # 绿灯：锚确认
        rec["outcome"] = "green"
        if use_llm:
            rec.update(_green_llm(code, year, field, value, sig, spec))
        return rec
    if not spec.anchor_key:                                     # 无锚字段：确定性判不了对错
        rec["outcome"] = "no_anchor"
        return rec
    rec["outcome"] = "non_green"                                # 有锚却没过 → 需诊断/人工
    if use_llm:
        rec.update(_nongreen_llm(code, year, field))
    return rec


def _green_llm(code, year, field, value, sig, spec) -> Dict:
    """绿灯 → 复核 agent 审盲区（含选错表/跨页体检）；pass → 入库，hold → 交人工。"""
    try:
        from src.agents.llm_judge import verify_field
        from src.eval.triage_queue import _auto_commit, enqueue
        v = verify_field(field, code, year, value, sig=sig, spec=spec)
        verdict = v.get("verdict")
        llm = {"llm_kind": "verify", "verdict": verdict,
               "suspects": v.get("suspects") or v.get("issues") or [], "summary": v.get("summary")}
        if verdict == "pass":
            return {"outcome": "committed", "committed": _auto_commit(code, year, field, value, sig), **llm}
        issues = ", ".join(str(s.get("issue")) for s in llm["suspects"] if s.get("issue"))[:120]
        enqueue(code, year, field, "needs_human", note=f"verify hold: {issues or llm['summary'] or ''}"[:200])
        return {"outcome": "verify_hold", "handed_to_human": True, **llm}
    except Exception as e:
        return {"llm_error": str(e)[:100]}


def _nongreen_llm(code, year, field) -> Dict:
    """非绿灯 → judge_diagnose 第一阶段（judge_chat 内已含 交人工/落台账 的确定性分流）。"""
    try:
        from src.agents.judge_diagnose_agent import prepare_judge_diagnose
        from src.console_service import judge_chat
        prep = prepare_judge_diagnose(code, year, field)
        if prep.get("error"):
            return {"llm_error": prep["error"]}
        res = judge_chat(code, year, field, prep["messages"])
        return {"llm_kind": "diagnose", "decision": res.get("decision"),
                "root_cause": res.get("root_cause"), "next_action": res.get("next_action"),
                "evidence": res.get("evidence"), "summary": res.get("summary"),
                "handed_to_human": res.get("handed_to_human")}
    except Exception as e:
        return {"llm_error": str(e)[:100]}


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


_FIELD_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd",
              "employees": "employee", "top_clients": "client", "top_suppliers": "supplier"}


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
    add("抽表", True, f"{len(tables)} 张表")

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
                       "rows": len(pick["table"]), "caption": (pick.get("caption") or "")[:60]})

    # 认证路由? (routed = 认证解析器命中且过硬规则)
    try:
        from src.parsers.revenue_router import route_field
        rt = route_field(spec, code, year)
    except Exception:
        rt = {"status": "error"}
    routed = rt.get("status") == "routed"

    # 解析（连溯源一起拿）
    if routed:
        value, prov = rt.get("result"), {}
    else:
        value, prov = _cold_parse_full(code, year, field, tables, pdf)
    if not value:
        add("解析", False, "解析器在选中表上没解出结构化数据")
        return {**out, "outcome": "no_data", "reason": "解析为空（选中表结构/认列失败）"}
    dims_present = list(value.keys()) if isinstance(value, dict) else ["<list>"]
    add("解析", True, {"via": "认证解析器" if routed else "冷启动", "dims": dims_present})

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
    anchor_ok = routed or conf == "high"
    add("锚判", anchor_ok, {"anchor": diag.get("anchor"), "confidence": conf,
                           "per_dim": per_dim, "missing_dims": missing,
                           "dims_agree": diag.get("dims_agree")})

    # 出口 + 失败原因
    if routed:
        outcome, reason = "green", "认证解析器命中且过硬规则 → 直接绿灯"
    elif conf == "high":
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
    return {**out, "outcome": outcome, "reason": reason, "via": "routed" if routed else "cold"}


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
    # green=过锚待复核；committed=复核pass已入库；verify_hold=复核否决(假绿灯)→人工
    anchored = cnt["green"] + cnt["committed"] + cnt["verify_hold"] + cnt["non_green"] + cnt["no_data"]
    success = cnt["green"] + cnt["committed"]      # 过锚且未被复核否决（复核跑完后 green→0，只剩 committed）
    return {"green": cnt["green"], "committed": cnt["committed"], "verify_hold": cnt["verify_hold"],
            "non_green": cnt["non_green"], "no_data": cnt["no_data"],
            "no_anchor": cnt["no_anchor"], "no_input": cnt["no_input"],
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
        if do_scan and get_tables(code, year) is None:            # 没缓存才扫（可断点续）
            pdf = _pdf(code, year)
            if pdf:
                try:
                    from src.parsers.infra.table_scanner import scan_pdf
                    from src.eval.table_cache import put as cache_put
                    cache_put(code, year, scan_pdf(pdf))
                except Exception as e:
                    log(f"{code} scan err {str(e)[:60]}")
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


def run_verify_pass(year: int = 2025, field: str = "revenue_breakdown",
                    fields: List[str] = None, log=print) -> Dict:
    """对已有结果里该字段的**绿灯**逐个跑复核 agent（选错表/跨页体检 + 逐项对照）：
    pass → 入库(committed)，hold → 交人工(verify_hold)。结果写回 + 实时进度。补齐"完整流程"的 LLM 那趟。"""
    import time
    fields = fields or ["revenue_breakdown", "cost_breakdown", "rnd_info"]
    res = load_result()
    if not res:
        return {"error": "无批量结果，先跑批量"}
    reports = res["reports"]
    codes = res.get("codes")
    targets = [(r, next(f for f in r["fields"] if f["field"] == field))
               for r in reports
               if any(f["field"] == field and f["outcome"] == "green" for f in r["fields"])]
    total = len(targets)
    state = {"phase": "verify", "total": total, "i": 0, "current": None,
             "done": [], "started": time.time(), "updated": time.time()}
    _write_progress(state)
    for i, (r, fo) in enumerate(targets, 1):
        code = r["code"]
        state.update(i=i, current=code, updated=time.time())
        _write_progress(state)
        rec = run_field(code, year, field, use_llm=True)          # 走绿灯 LLM 路径：复核→入库/人工
        fo["outcome"] = rec.get("outcome", fo["outcome"])
        fo["verify"] = {k: rec.get(k) for k in ("verdict", "summary", "handed_to_human", "suspects")
                        if rec.get(k) is not None}
        try:
            save_verify_run(code, year, field, rec)               # 复核结论写 DB
        except Exception:
            pass
        state["done"].append({"code": code, "verdict": rec.get("verdict"), "outcome": rec.get("outcome")})
        state.update(updated=time.time())
        _write_progress(state)
        agg = _aggregate(reports, fields, year)
        if codes:
            agg["codes"] = codes
        save_result(agg)
        log(f"[{i}/{total}] {code} verify={rec.get('verdict')} → {rec.get('outcome')}")
    state.update(phase="done", current=None, updated=time.time())
    _write_progress(state)
    return load_result()


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
    verify = {k: rec.get(k) for k in ("verdict", "summary", "handed_to_human", "suspects")
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
