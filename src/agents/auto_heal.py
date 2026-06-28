"""
自动自愈 — 把"构建期人给 golden"自动化：LLM 从原表抽 golden(锚验证) → repair 写解析器 → 认证

让批处理能走**完整生产流程**：解析失败的字段 → 这里自动用 LLM 救。
关键：LLM 抽出的 golden 必须**过 DB 跨表锚**(分项和≈营业收入/成本/研发)才算可信，
      绝不拿没验证的当真值(正确率优先)。无锚字段(客户/供应商)抽不了 → 转人工。
"""

import json
import re
from typing import Dict, Optional

from src.agents.llm_client import chat
from src.eval.table_cache import get_tables
from src.parsers.infra.table_scanner import filter_by_signature
from src.parsers.revenue_router import field_plausibility, route_field
from src.eval.anchors import get_anchors

_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd",
        "employees": "employee"}


def _shape(spec) -> str:
    a = spec.amount_key
    if spec.cls == "B":
        return ('{"%s": 合计数, "%s": [{"name":科目, "%s": 金额}, ...]}'
                % (spec.total_key, spec.detail_key, a))
    if spec.dims:
        return ('{"%s": [{"name":分项名, "%s":金额, "ratio_pct":占比}], ...其余维度键: %s}'
                % (spec.dims[0], a, "/".join(spec.dims)))
    return '[{"name":分项名, "%s":金额, "ratio_pct":占比}, ...]' % a


def _serialize(grid, max_rows=40) -> str:
    out = []
    for row in grid[:max_rows]:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            out.append(" | ".join(c for c in cells if c))
    return "\n".join(out)


def _extract_json(raw: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    txt = (m.group(1) if m else raw).strip()
    if not (txt.startswith("{") or txt.startswith("[")):
        i = min([x for x in (txt.find("{"), txt.find("[")) if x >= 0] or [-1])
        j = max(txt.rfind("}"), txt.rfind("]"))
        if i >= 0 and j > i:
            txt = txt[i:j + 1]
    try:
        return json.loads(txt)
    except Exception:
        return None


def llm_extract_golden(spec, code: str, year: int, log=print) -> Optional[Dict]:
    """LLM 从候选原表抽出本字段的 golden；只有**过 DB 锚**(置信 high)才返回，否则 None。"""
    if not spec.anchor_key:                       # 无锚字段(客户/供应商)抽了也没法验 → 不抽
        return None
    tables = get_tables(code, year)
    if not tables:
        return None
    cands = filter_by_signature(tables, _SIG.get(spec.field, "revenue"))[:2]
    anchors = get_anchors(code, year)
    for cand in cands:
        table_text = _serialize(cand.get("table") or [])
        if not table_text:
            continue
        prompt = (
            f"从下面这张年报表格里，抽取 **{year}年(本期，不是上期/去年)** 的「{spec.label}」结构化数据。\n"
            f"准则口径：{spec.spec_note}\n"
            f"输出 JSON，形如：{_shape(spec)}\n"
            f"要点：①只要本期({year}年)那一列，别拿上期/去年；②金额单位换算成元；"
            f"③占比是百分数(如60.8表示60.8%)；④跳过合计/小计行；⑤分项要全。\n"
            f"只输出 JSON，不要解释。\n\n表格：\n{table_text}"
        )
        try:
            raw = chat([{"role": "system", "content": "你从中文年报表格精确抽取结构化数据，严谨、只输出JSON。"},
                        {"role": "user", "content": prompt}], role="extract", temperature=0)
        except Exception as e:
            log(f"    LLM抽取异常: {str(e)[:80]}")
            continue
        val = _extract_json(raw)
        if val is None:
            continue
        sig = field_plausibility(spec, val, anchors)
        log(f"    LLM抽取 {spec.label}: 锚验证 confidence={sig.get('confidence')}")
        if sig.get("confidence") == "high":       # 分项和≈DB锚 → 可信 golden
            return val
    return None


def auto_heal_field(spec, code: str, year: int, log=print) -> Dict:
    """单字段自动自愈：已 routed 且可信→跳过；否则 LLM 抽 golden→repair 写解析器→认证。"""
    base = {"code": code, "year": year, "field": spec.field}
    rt = route_field(spec, code, year)
    if rt["status"] == "routed" and (rt.get("signal") or {}).get("confidence") != "low":
        return {**base, "action": "routed", "status": "ok"}

    golden = llm_extract_golden(spec, code, year, log=log)
    if golden is None:
        return {**base, "action": "escalate", "status": "needs_human", "reason": "LLM抽golden未过锚"}

    try:
        from src.agents.code_generator import repair
        from src.eval.parser_catalog import certify
        from src.eval.route_index import fingerprint_of
        from src.eval.triage_queue import resolve, record_ok
        out_path = f"src/parsers/versions/{spec.version_prefix}_{code}_{year}.py"
        # golden_entry 必须带 stock_code/year/_status(否则 eval_version 的 only_confirmed 会滤空→越界)
        golden_entry = {"stock_code": code, "year": year, "_status": "confirmed_auto", spec.field: golden}
        r = repair(code, year, golden_entry, lambda c, y: None, out_path, spec=spec, log=log)
    except Exception as e:
        import traceback
        log(f"  {code}/{spec.label}: 自愈异常\n" + "\n".join(traceback.format_exc().strip().splitlines()[-6:]))
        return {**base, "action": "escalate", "status": "needs_human",
                "reason": f"自愈异常: {str(e)[:100]}"}
    if r.get("accepted"):
        key = f"{code}-{year}-{spec.field}-自愈"
        fp = fingerprint_of(code, year)
        certify(key, r.get("parser") or out_path, field=spec.field, fingerprints=[fp] if fp else None)
        resolve(code, year, spec.field)
        record_ok(code, year, spec.field, {"confidence": "high"})
        log(f"  {code}/{spec.label}: 🎓 LLM自愈到 exact → 认证")
        return {**base, "action": r.get("action"), "status": "certified"}
    return {**base, "action": "escalate", "status": "needs_human",
            "reason": f"LLM写解析器未到exact(最好{r.get('best_score')})"}
