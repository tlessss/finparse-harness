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
from src.agents.llm_routing import resolve_model
from src.prompts.registry import build_messages
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


_UNIT_NAME = {1000: "千元", 10000: "万元", 100000000: "亿元"}


def _scale_amounts(val, spec, ratio):
    """把抽出的所有金额 ×ratio(单位换算兜底);占比不动。"""
    import copy
    v = copy.deepcopy(val)
    amt = spec.amount_key

    def _rows(rows):
        for r in rows:
            if isinstance(r, dict) and isinstance(r.get(amt), (int, float)):
                r[amt] = r[amt] * ratio

    if isinstance(v, dict):
        for k, rows in v.items():
            if isinstance(rows, list):
                _rows(rows)
            elif k == getattr(spec, "total_key", None) and isinstance(rows, (int, float)):
                v[k] = rows * ratio
    elif isinstance(v, list):
        _rows(v)
    return v


def llm_extract_golden(spec, code: str, year: int, log=print) -> Optional[Dict]:
    """LLM 从候选原表抽出本字段的 golden；只有**过 DB 锚**(置信 high)才返回，否则 None。
    单位：检测表单位(千元/万元)明确提示 LLM；并做确定性兜底(没换算就 ×ratio 再判)。"""
    if not spec.anchor_key:                       # 无锚字段(客户/供应商)抽了也没法验 → 不抽
        return None
    from src.parsers.infra.unit_detector import detect_unit
    tables = get_tables(code, year)
    if not tables:
        return None
    cands = filter_by_signature(tables, _SIG.get(spec.field, "revenue"))[:2]
    anchors = get_anchors(code, year)
    for cand in cands:
        table_text = _serialize(cand.get("table") or [])
        if not table_text:
            continue
        ratio = detect_unit(table_text)
        unit_hint = (f"⚠本表金额单位是【{_UNIT_NAME.get(ratio, str(ratio) + '元')}】，"
                     f"每个金额必须×{ratio}换算成【元】再输出(例:表里写1,234→输出{1234 * ratio})。\n"
                     if ratio > 1 else "")
        messages = build_messages("auto_heal", {
            "year": year, "unit_hint": unit_hint, "label": spec.label, "spec_note": spec.spec_note,
            "shape": _shape(spec), "table_text": table_text,
        })["messages"]
        try:
            raw = chat(messages, role="extract", temperature=0, model=resolve_model("auto_heal"))
        except Exception as e:
            log(f"    LLM抽取异常: {str(e)[:80]}")
            continue
        val = _extract_json(raw)
        if val is None:
            continue
        sig = field_plausibility(spec, val, anchors)
        # 单位兜底：没过锚但表是千元/万元 → 试 ×ratio 再判(LLM 漏换算的确定性补救)
        if sig.get("confidence") != "high" and ratio > 1:
            val2 = _scale_amounts(val, spec, ratio)
            sig2 = field_plausibility(spec, val2, anchors)
            if sig2.get("confidence") == "high":
                val, sig = val2, sig2
                log(f"    （单位兜底 ×{ratio} 生效）")
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
    # 没到 exact → 删掉失败 repair 留下的孤儿文件(没认证、没用)
    import os
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass
    return {**base, "action": "escalate", "status": "needs_human",
            "reason": f"LLM写解析器未到exact(最好{r.get('best_score')})"}
