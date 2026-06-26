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
    _write(s)
    return s


def _pdf_for(code: str, year: int):
    hits = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    return hits[0] if hits else None


def _push_recent(state: Dict, rec: Dict) -> None:
    state["recent"] = ([rec] + state.get("recent", []))[:20]


def run_batch(codes: List[str], year: int = 2025, db_write: bool = False,
              log: Callable = print) -> Dict:
    """跑一批报告。返回最终状态(进度+分布)。起停由 control() 写的标志控制。"""
    from src.engine_orchestrator import FinParseAI
    from src.eval.triage_queue import triage_report
    from src.eval.table_cache import get_tables

    eng = FinParseAI()
    state = {"running": True, "paused": False, "stopped": False,
             "total": len(codes), "done": 0, "skipped": 0, "errors": 0,
             "fields_routed": 0, "by_reason": {},
             "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "recent": [], "current": None}
    _write(state)

    for code in codes:
        st = _read()
        if st.get("stopped"):
            log(f"  批量已停止于 {state['done']}/{state['total']}")
            break
        while st.get("paused") and not st.get("stopped"):     # 暂停 → 等到 resume/stop
            time.sleep(2)
            st = _read()
        if st.get("stopped"):
            break
        state["current"] = code
        _write({**state, "paused": st.get("paused", False)})

        pdf = _pdf_for(code, year)
        if pdf is None:                                        # 没 PDF → 跳过(标记)
            state["skipped"] += 1
            _push_recent(state, {"code": code, "status": "no_pdf", "needs": []})
        else:
            try:
                pre = get_tables(code, year)                   # 缓存表则不重扫
                out = eng.run(pdf, stock_code=code, report_year=year,
                              db_write=db_write, pre_scan=pre)
                recs = triage_report(code, year)               # 落盘待办
                reasons = [r["reason"] for r in recs]
                for r in reasons:
                    state["by_reason"][r] = state["by_reason"].get(r, 0) + 1
                routed_n = sum(1 for v in (out.get("parse_flags") or {}).values() if v == "ok")
                state["fields_routed"] += routed_n
                _push_recent(state, {"code": code, "status": "ok",
                                     "routed_n": routed_n, "needs": reasons})
            except Exception as e:
                state["errors"] += 1
                _push_recent(state, {"code": code, "status": "error", "error": str(e)[:120]})
                log(f"  {code} 解析异常: {str(e)[:80]}")

        state["done"] += 1
        state["current"] = None
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(state)
        log(f"  [{state['done']}/{state['total']}] {code} done")

    state["running"] = False
    state["current"] = None
    state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write(state)
    return state
