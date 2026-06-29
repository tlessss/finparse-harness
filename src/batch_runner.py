"""
批量跑批器 — 遍历一批报告：引擎解析 → 写分诊队列 → 进度/分布

定位：前端就绪后"小批量试点(50-100份)"用的编排器。**只跑解析+填队列，不自动改代码、
不无人值守**(安全)。起停暂停用文件标志(goldset/batch_state.json)，脚本/API 任意进程可控。

每份：engine.run(解析,缓存表) → triage_report(把 needs_write/low_confidence 落盘) → 累计分布。
"""

import glob
import json
import os
import time
from typing import Callable, Dict, List

from src.config import Config

_STATE = "goldset/batch_state.json"


def _read() -> Dict:
    if os.path.exists(_STATE):
        try:
            return json.load(open(_STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write(s: Dict) -> None:
    os.makedirs(os.path.dirname(_STATE) or ".", exist_ok=True)
    json.dump(s, open(_STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def progress() -> Dict:
    return _read() or {"running": False, "done": 0, "total": 0}


def control(action: str) -> Dict:
    """起停暂停：pause|resume|stop（写进状态文件，run_batch 每份前读）。"""
    s = _read()
    if action == "pause":
        s["paused"] = True
    elif action == "resume":
        s["paused"] = False
    elif action == "stop":
        s["stopped"], s["paused"] = True, False
        s["awaiting"] = False           # 立刻清单步暂停态 → 前端暂停面板马上消失
        s["step_data"] = None
    _write(s)
    return s


def _pdf_for(code: str, year: int):
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def _push_recent(state: Dict, rec: Dict) -> None:
    state["recent"] = ([rec] + state.get("recent", []))[:20]


def _save_progress(state: Dict) -> None:
    """写进度时**保留磁盘上的控制标志**(stopped/paused/step_continue 归 control/前端管)，避免覆盖。"""
    disk = _read()
    _write({**state, "stopped": disk.get("stopped", False),
            "paused": disk.get("paused", False),
            "step_continue": disk.get("step_continue", False)})


def _breakpoint(state: Dict, stage: str, data, log: Callable) -> bool:
    """单步断点：写出该阶段详细数据、置 awaiting，等前端"继续"(step_continue)或"停止"。
    返回 True=继续 / False=停止。"""
    state["stage"] = stage
    state["step_data"] = data
    state["awaiting"] = True
    _save_progress(state)
    log(f"  ⏸ 断点[{stage}] 等待确认…")
    while True:
        time.sleep(1)
        disk = _read()
        if disk.get("stopped"):
            return False
        if disk.get("step_continue"):
            disk["step_continue"] = False               # 消费掉这次"继续"
            disk["awaiting"] = False
            disk["step_data"] = None
            _write(disk)
            state["awaiting"] = False
            return True


def _table_preview(tables, year: int = None, judge: bool = False) -> Dict:
    """抽表阶段给前端看的数据：每个字段的候选表(页码+预览)。judge=True 时额外让 LLM 判
    '是不是目标表/抽取干不干净'，把挑错表/抽错位在解析前就标出来。"""
    from src.parsers.infra.table_scanner import filter_by_signature
    out: Dict = {"_total_tables": len(tables or [])}
    fields = [("营收", "revenue", "revenue_breakdown"), ("成本", "cost", "cost_breakdown"),
              ("研发", "rnd", "rnd_info"), ("员工", "employee", "employees")]
    for label, sig, field in fields:
        cands = filter_by_signature(tables or [], sig)[:1]
        if cands:
            t = cands[0]
            rows = [[(c or "").replace("\n", " ").strip()[:14] for c in row]
                    for row in (t.get("table") or [])[:8]]
            entry = {"page": t.get("page"), "rows": rows}
            if judge:
                from src.agents.extract_judge import judge_extraction
                from src.eval.field_spec import get_spec
                entry["verdict"] = judge_extraction(get_spec(field), t, year)
            out[label] = entry
    return out


def _result_preview(out: Dict, fields: Dict) -> Dict:
    """解析+判定阶段给前端看的数据：每个字段 状态/来源/置信度/锚 + 条数。"""
    sigs = out.get("signals") or {}
    fld = {}
    for f in ("revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"):
        v = out.get(f)
        n = (sum(len(x) for x in v.values() if isinstance(x, list)) if isinstance(v, dict)
             else (len(v) if isinstance(v, list) else 0))
        s = sigs.get(f) or {}
        fld[f] = {"status": fields.get(f), "source": s.get("source"),
                  "confidence": s.get("confidence"), "anchored": s.get("anchored"), "n": n}
    return {"fields": fld}


def run_batch(codes: List[str], year: int = 2025, db_write: bool = False,
              heal: bool = False, step: bool = False, log: Callable = print) -> Dict:
    """跑一批报告。返回最终状态(进度+分布)。起停由 control() 写标志、本函数只读不覆盖。"""
    from src.engine_orchestrator import FinParseAI
    from src.eval.triage_queue import triage_report
    from src.eval.table_cache import get_tables

    eng = FinParseAI()
    _write({"running": False})                       # 清掉上一轮可能残留的 stopped/paused
    state = {"running": True, "total": len(codes), "done": 0, "skipped": 0, "errors": 0,
             "fields_with_data": 0, "by_reason": {},
             "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "recent": [], "current": None}
    _save_progress(state)

    for code in codes:
        if _read().get("stopped"):                   # 控制标志只从磁盘读(control 写的)
            log(f"  批量已停止于 {state['done']}/{state['total']}")
            break
        while _read().get("paused") and not _read().get("stopped"):
            time.sleep(2)
        if _read().get("stopped"):
            break
        state["current"] = code
        state["stage"] = None
        _save_progress(state)

        pdf = _pdf_for(code, year)
        if pdf is None:                              # 没 PDF → 跳过(标记)
            state["skipped"] += 1
            _push_recent(state, {"code": code, "status": "no_pdf", "fields": {}})
        else:
            try:
                pre = get_tables(code, year)         # 缓存表则不重扫
                # 单步断点①：抽表 —— 给人看候选表 + LLM 判定(是不是目标表/抽得干不干净),不对就停
                if step and not _breakpoint(state, "抽表", _table_preview(pre, year, judge=True), log):
                    break

                def _stage(field):                   # 引擎每解析一个字段就回调 → 实时上报
                    state["stage"] = field
                    _save_progress(state)

                out = eng.run(pdf, stock_code=code, report_year=year,
                              db_write=db_write, pre_scan=pre, on_stage=_stage)
                recs = triage_report(code, year)     # 落盘台账
                # 逐字段结果：ok(绿) / needs_write(红) / unverified(黄) / low_confidence(橙)
                fields = {r["field"]: ("ok" if r["status"] == "ok" else r["reason"]) for r in recs}
                # 单步断点②：解析+判定 —— 给人看每字段解出什么/置信度,确认没问题再往下(自愈/写库)
                if step and not _breakpoint(state, "解析+判定", _result_preview(out, fields), log):
                    break
                # ── 完整流程：对不可信且有锚的字段，自动走 LLM 自愈(抽golden→写解析器→认证) ──
                if heal:
                    from src.agents.auto_heal import auto_heal_field
                    from src.eval.field_spec import get_spec
                    for r in recs:
                        if r["status"] == "open" and r["reason"] in ("needs_write", "low_confidence"):
                            spec = get_spec(r["field"])
                            if not spec.anchor_key:          # 无 DB 锚(客户/供应商/员工)→救不了,跳过
                                continue
                            state["stage"] = f"自愈·{spec.label}"
                            _save_progress(state)
                            hr = auto_heal_field(spec, code, year, log=log)
                            if hr["status"] == "certified":
                                fields[r["field"]] = "healed"        # 自愈成功(LLM写出并认证)
                for f, st in fields.items():
                    if st in ("needs_write", "low_confidence", "unverified"):
                        state["by_reason"][st] = state["by_reason"].get(st, 0) + 1
                n_data = sum(1 for v in (out.get("parse_flags") or {}).values() if v == "ok")
                state["fields_with_data"] += n_data
                _push_recent(state, {"code": code, "status": "ok", "fields": fields})
            except Exception as e:
                state["errors"] += 1
                _push_recent(state, {"code": code, "status": "error", "error": str(e)[:120]})
                log(f"  {code} 解析异常: {str(e)[:80]}")

        state["done"] += 1
        state["current"] = None
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_progress(state)
        log(f"  [{state['done']}/{state['total']}] {code} done")

    state["running"] = False
    state["current"] = None
    state["awaiting"] = False          # 清单步暂停态(否则停止后前端暂停面板还赖着,看着像没停)
    state["step_data"] = None
    state["stage"] = None
    state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_progress(state)
    return state
