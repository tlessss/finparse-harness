"""
分诊队列 — 记录哪些(报告,字段)需要 LLM 写/改解析器，持久化、可销账

把原本"每次现算、跑完就没"的 needs_repair/低置信信号**落盘成一张待办表**，
让"哪些要 LLM 处理"变得可见、可分配、可追踪。批量扫描写入；自愈/控制台消费；
解析器认证后销账(resolve)。

一条记录：{code, year, field, reason, signal, status, created_at, updated_at}
  reason ∈ needs_write(无解析器达标→新建/fork) | low_confidence(#1锚对不上→改/复核)
         | suspicious(#2 LLM裁判判可疑→改) | needs_human(无 golden/修复失败)
  status ∈ open → in_progress → resolved
"""

import json
import os
import time
from typing import Dict, List, Optional

_QUEUE = "goldset/triage_queue.json"
REASONS = ("needs_write", "low_confidence", "suspicious", "needs_human")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load() -> List[Dict]:
    if os.path.exists(_QUEUE):
        return json.load(open(_QUEUE, encoding="utf-8")).get("records", [])
    return []


def _save(recs: List[Dict]) -> None:
    os.makedirs(os.path.dirname(_QUEUE) or ".", exist_ok=True)
    json.dump({"records": recs}, open(_QUEUE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def _key(r: Dict):
    return (r["code"], r["year"], r["field"])


def _sig(signal) -> Optional[Dict]:
    """只留信号里有用的几个字段，别把整坨塞进队列。"""
    if not isinstance(signal, dict):
        return None
    return {k: signal[k] for k in ("clean", "confidence", "anchored", "diff_pct", "reason")
            if k in signal}


def enqueue(code: str, year: int, field: str, reason: str,
            signal=None, note: str = "") -> Dict:
    """登记/更新一条待办(按 code+year+field 去重)。已 resolved 的再次出问题会重开。"""
    recs = _load()
    for r in recs:
        if _key(r) == (code, year, field):
            if r["status"] == "resolved":
                r["status"] = "open"
            r.update(reason=reason, signal=_sig(signal), note=note, updated_at=_now())
            _save(recs)
            return r
    rec = {"code": code, "year": year, "field": field, "reason": reason,
           "signal": _sig(signal), "note": note, "status": "open",
           "created_at": _now(), "updated_at": _now()}
    recs.append(rec)
    _save(recs)
    return rec


def resolve(code: str, year: int, field: str) -> bool:
    """销账：该(报告,字段)已被解析器搞定(如认证后)。"""
    recs = _load()
    changed = False
    for r in recs:
        if _key(r) == (code, year, field) and r["status"] != "resolved":
            r["status"] = "resolved"
            r["updated_at"] = _now()
            changed = True
    if changed:
        _save(recs)
    return changed


def set_status(code: str, year: int, field: str, status: str) -> bool:
    recs = _load()
    for r in recs:
        if _key(r) == (code, year, field):
            r["status"] = status
            r["updated_at"] = _now()
            _save(recs)
            return True
    return False


def list_open(reason: str = None, field: str = None) -> List[Dict]:
    return [r for r in _load() if r["status"] != "resolved"
            and (reason is None or r["reason"] == reason)
            and (field is None or r["field"] == field)]


def summary() -> Dict:
    recs = _load()
    openr = [r for r in recs if r["status"] != "resolved"]
    by_reason, by_field = {}, {}
    for r in openr:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        by_field[r["field"]] = by_field.get(r["field"], 0) + 1
    return {"total": len(recs), "open": len(openr),
            "by_reason": by_reason, "by_field": by_field}


def triage_report(code: str, year: int, fields: List[str] = None) -> List[Dict]:
    """对一份**已抽表缓存**的报告分诊：逐字段路由 → 把问题落盘。返回本次产生的待办。
    needs_repair→needs_write；routed 但低置信→low_confidence；routed 且不低→销账。"""
    from src.eval.table_cache import get_tables
    from src.parsers.revenue_router import route_field
    from src.eval.field_spec import FIELDS
    if get_tables(code, year) is None:
        return []                                  # 没抽表无法分诊(交给引擎先 scan)
    out = []
    for fname, spec in FIELDS.items():
        if fields and fname not in fields:
            continue
        rt = route_field(spec, code, year)
        if rt["status"] == "needs_repair":
            out.append(enqueue(code, year, fname, "needs_write", rt.get("signal")))
        elif rt["status"] == "routed":
            if (rt.get("signal") or {}).get("confidence") == "low":
                out.append(enqueue(code, year, fname, "low_confidence", rt.get("signal")))
            else:
                resolve(code, year, fname)         # 现在没问题 → 若之前有待办则销账
    return out


def db_needs_write(field_col: str, year: int = 2025, limit: int = 200) -> List[str]:
    """从 DB 直接捞"从没解出来"的报告(该字段列为空) = 天然的 needs_write 初始清单。"""
    try:
        from src.database import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT stock_code FROM financial_reports "
                            f"WHERE report_year=%s AND ({field_col} IS NULL OR {field_col}='') "
                            f"LIMIT %s", (year, limit))
                return [r["stock_code"] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []
