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
               "confidence": (rt.get("signal") or {}).get("confidence"), "value": rt.get("result")}
        if use_llm:
            rec.update(_green_llm(code, year, field, rt.get("result"), rt.get("signal") or {}, spec))
        return rec

    # ② 冷启动兜底：无认证解析器时引擎默认解析器 + 锚判。
    #    base 优先 → 不过锚扫规则版本池（base-first 结构上不回归）。
    pv = _parse_versioned(code, year, field, tables, pdf, anchors, spec)
    value, sig = pv["value"], pv["sig"]
    if not value:
        return {"field": field, "outcome": "no_data", "via": "cold"}   # 解析器没解出东西 → needs_write
    conf = sig.get("confidence")
    rec = {"field": field, "via": "cold", "confidence": conf, "anchored": sig.get("anchored"),
           "value": value, "rule_version": pv["version"], "version_sweep": pv["sweep"]}

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
            enqueue(code, year, field, "needs_human", note="选表自愈未选到更好的表")
            return {"outcome": "verify_hold", "handed_to_human": True, **llm, **rec}
        issues = ", ".join(str(s.get("issue")) for s in suspects if s.get("issue"))[:120]
        enqueue(code, year, field, "needs_human", note=f"verify hold: {issues or llm['summary'] or ''}"[:200])
        return {"outcome": "verify_hold", "handed_to_human": True, **llm}
    except Exception as e:
        return {"llm_error": str(e)[:100]}


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


def _nongreen_llm(code, year, field) -> Dict:
    """非绿灯 → **按诊断归类派 healer**(Tier 1 路由器):
      过计 → 直接 L2 切桶(选表自愈救不了过计);无锚 → 直接诊断(选表/L2 都靠锚,没锚白跑,取锚自愈 Phase2 再接);
      其余(选错表/全空/严重缺/单维不齐/…)→ 选表自愈优先 → still_bad 则 L2 → 仍不行诊断。
    每个 healer 内部仍走过锚+复核双闸。结果带 routed_cat(诊断归类,前端可见)。"""
    try:
        spec = get_spec(field)
        cat = _diagnose_category(code, year, field)

        # 无锚(取不到营收锚,多为金融股):选表/L2 都要靠锚判过没过,没锚白跑 → 直接诊断(Phase2 接取锚自愈)。
        if cat == "无锚":
            out = _run_diagnose(code, year, field)
            out["routed_cat"] = cat
            out["note"] = "无锚(待 Phase2 取锚自愈/金融域外)"
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
        if heal.get("outcome") == "still_bad" and chosen_tbl:
            rh_rec = _rule_heal_and_verify(code, year, field, chosen_tbl, spec)
            if rh_rec:
                return {**rh_rec, "heal_probe": heal, "routed_cat": cat}
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


def heal_select(code, year, field="revenue_breakdown") -> Dict:
    """选表自愈：LLM 选表 agent 重选 → forced 重解析 → 重判锚。
    outcome ∈ green(重选后过锚) | caliber_gap(选对但主营口径差) | still_bad(仍不对)。"""
    from src.agents.select_table_agent import select_table_llm
    sr = select_table_llm(code, year, field, debug=True)
    select_chat = {"prompt": sr.get("_prompt"), "reply": sr.get("_raw")}   # 选表 agent 的对话留痕
    chosen = sr.get("chosen_table")
    if not chosen or not chosen.get("table"):
        return {"healed": False, "outcome": "no_pick", "select_reason": sr.get("reason"),
                "select_chat": select_chat}
    tables, pdf = get_tables(code, year), _pdf(code, year)
    from src.eval.anchors import get_anchors
    anchors = get_anchors(code, year) or {}
    value = _reparse_forced(code, year, field, chosen, tables, pdf)
    sig = field_plausibility(get_spec(field), value or {}, anchors)
    stitched = None
    if sig.get("confidence") != "high":                            # 不过锚 → 试跨页续表拼接(锚闸兜底)
        chosen, value, sig, stitched = _stitch_for_anchor(
            code, year, field, chosen, tables, pdf, anchors, value, sig)
    conf = sig.get("confidence")
    outcome = "green" if conf == "high" else ("caliber_gap" if sr.get("caliber_gap") else "still_bad")
    from src.agents.llm_judge import _grid_to_text
    return {"healed": True, "outcome": outcome, "chosen_page": sr.get("chosen_page"),
            "chosen_caption": sr.get("chosen_caption"), "select_stage": sr.get("stage"),
            "select_reason": sr.get("reason"), "caliber_gap": sr.get("caliber_gap"),
            "value": value, "confidence": conf, "select_chat": select_chat,
            "stitched_pages": stitched,                             # 跨页拼接了哪些页(None=没拼)
            "_chosen_table": chosen,                                # 选中表对象(内部用:L2改规则在它上面重解;持久化前会 pop)
            "source": _grid_to_text(chosen.get("table") or [])}     # 纠正后那张表 → 给复核当源文


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
    """选表自愈:选表 agent 重选 → 重解析 → 若选到更好的表(过锚/口径差)→ 复核(信任源文,只核数据) → 入库/人工。
    返回 (rec, healed)：healed=False 表示没选到更好的表(仍 still_bad/no_pick),交回上层走诊断。"""
    from src.agents.llm_judge import verify_field
    from src.eval.triage_queue import _auto_commit, enqueue
    heal = heal_select(code, year, field)
    src = heal.pop("source", None)
    if src:
        heal["source_preview"] = "\n".join(src.split("\n")[:24])       # 重选那张表(溯源前24行),给前端
    ho = heal.get("outcome")
    if ho in ("green", "caliber_gap") and heal.get("value"):           # 选到更好的表 → 走复核
        v2 = verify_field(field, code, year, heal["value"], spec=spec,
                          source_override=src, extra_note=_TRUST_NOTE, debug=True)
        rd = {"verdict": v2.get("verdict"), "suspects": v2.get("suspects") or [], "summary": v2.get("summary")}
        common = {"llm_kind": "verify", "healed_select": True, "heal": heal,
                  "reverify": v2.get("verdict"), "reverify_detail": rd,
                  "reverify_chat": {"system": v2.get("_system"), "prompt": v2.get("_prompt"), "reply": v2.get("_raw")}}
        if v2.get("verdict") == "pass":
            return {"outcome": "committed", "caliber_gap": (ho == "caliber_gap"),
                    "committed": _auto_commit(code, year, field, heal["value"], {"confidence": "high"}), **common}, True
        enqueue(code, year, field, "needs_human", note=f"选表自愈→{ho}→复核hold p{heal.get('chosen_page')}"[:200])
        return {"outcome": "verify_hold", "handed_to_human": True, **common}, True
    return {"heal": heal}, False                                       # 没更好的表 → 交回诊断


def _rule_heal_and_verify(code, year, field, chosen, spec) -> Optional[Dict]:
    """L2 改规则自愈:选对表但 base 解错 → LLM 提规则 delta → 合并重解过锚 → 复核(信任源文) →
    pass 则入库 + save_version 把这条规则固化进池;否则交人工。
    返回 rec(dict)：outcome ∈ committed / verify_hold；None 表示 delta 也修不了(交回诊断/人工)。"""
    from src.agents.rule_heal_agent import rule_heal
    from src.agents.llm_judge import verify_field, _grid_to_text
    from src.eval.triage_queue import _auto_commit, enqueue
    from src.parsers.infra.rule_versions import save_version
    rh = rule_heal(code, year, field, chosen, debug=True)
    common = {"llm_kind": "rule_heal",
              "rule_heal": {k: rh.get(k) for k in
                            ("outcome", "delta", "note", "reason", "fixable_claim", "dim_diff_after")},
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
        path = save_version(_RULE_FILE.get(field, "revenue"), vid,
                            {_RULE_KEY.get(field, "revenue_breakdown"): rh["delta"]},
                            note=rh.get("note"), meta={"origin": f"{code}_{year}"})
        return {"outcome": "committed", "healed_rule": True,
                "committed": _auto_commit(code, year, field, rh["value"], {"confidence": "high"}),
                "rule_version_saved": vid, "rule_version_path": path, **common}
    enqueue(code, year, field, "needs_human", note=f"L2改规则过锚但复核hold p{chosen.get('page')}"[:200])
    return {"outcome": "verify_hold", "handed_to_human": True, **common}


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


def _classify_failure(o, reason, v, anchor, per_dim, best, ndim) -> str:
    """从 DB 已存的 chain(锚判逐维) + verify(复核疑点) 给失败归类——不重解。"""
    susp = (v.get("suspects") or []) + ((v.get("reverify_detail") or {}).get("suspects") or [])
    iss = {s.get("issue") for s in susp if s.get("issue")}
    if o == "no_such_table":
        return "真无表"
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
    "无锚":      ("金融股(银行/券商),无营收构成锚", "移出失败 / 取锚自愈", "out"),
    "过计":      ("某桶和 >锚(多维堆一桶/合计混入)", "L2切桶", "have"),
    "选错表":    ("复核判选错表,自愈没救回", "选表自愈多轮", "light"),
    "严重缺":    ("仅解出<50%锚(选到小表/大漏行)", "选表自愈多轮 / 抽表自愈", "new"),
    "取错列/单位": ("金额取错列或单位没换算", "L2扩单位override", "light"),
    "抠错名称":  ("名称列抠错", "L2加name别名", "light"),
    "中度缺":    ("解出50~90%锚(漏行/漏维)", "跨页拼接 / 抽表自愈", "new"),
    "真无表":    ("报告确无营收构成表", "正确判缺失(已达成)", "have"),
}
_CAT_ORDER = ["单维不齐", "全空", "无锚", "过计", "选错表", "严重缺", "取错列/单位", "抠错名称", "中度缺", "真无表"]


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
    denom = sum(tally.values()) - tally.get("no_such_table", 0) - tally.get("no_input", 0) - by_cat.get("无锚", 0)
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
    # green=过锚待复核；committed=复核pass已入库；verify_hold=复核否决(假绿灯)→人工
    # no_such_table=选表agent确认源文无此构成表 → 剔出分母(数据不存在,不算失败)
    anchored = cnt["green"] + cnt["committed"] + cnt["verify_hold"] + cnt["non_green"] + cnt["no_data"]
    success = cnt["green"] + cnt["committed"]      # 过锚且未被复核否决（复核跑完后 green→0，只剩 committed）
    return {"green": cnt["green"], "committed": cnt["committed"], "verify_hold": cnt["verify_hold"],
            "non_green": cnt["non_green"], "no_data": cnt["no_data"],
            "no_anchor": cnt["no_anchor"], "no_input": cnt["no_input"], "no_such_table": cnt["no_such_table"],
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


_ANCHORED_GREEN = ("green", "committed", "verify_hold")


def run_full_pass(year: int = 2025, field: str = "revenue_breakdown",
                  codes: List[str] = None, log=print) -> Dict:
    """对 DB 里全部报告跑**完整 LLM 流水线**：绿灯→复核(+选表自愈)，非绿灯→先选表 agent 再诊断。写 DB + 进度。"""
    import time
    from src.eval.test_store import list_latest_runs
    if codes is None:
        codes = sorted({r["stock_code"] for r in list_latest_runs(year, [field]) if r["field"] == field})
    total = len(codes)
    state = {"phase": "verify", "total": total, "i": 0, "current": None,
             "done": [], "started": time.time(), "updated": time.time()}
    _write_progress(state)
    for i, code in enumerate(codes, 1):
        state.update(i=i, current=code, updated=time.time())
        _write_progress(state)
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
                                      "value", "chat", "reverify_chat", "diag_chat", "routed_cat")
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
