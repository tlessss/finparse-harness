"""
覆盖台账 / 分诊队列 — 记录每个(报告,字段)的状态：可信的 + 要干活的都记

不只装"问题"，而是**全量台账**：让控制台既能看"哪些要 LLM 写/改"(红/橙)，
也能看"哪些已可信"(绿)——绿色覆盖率才是核心指标(净通过率的故事)，给操作者安全感。

一条记录：{code, year, field, reason, status, signal, created_at, updated_at}
  status ∈ ok(可信:已认证routed+硬规则过) | open(要干活) | resolved(原问题已修)
  reason(status=open 时的分类) ∈ needs_write(无解析器) | low_confidence(锚对不上)
                                | suspicious(#2判可疑) | needs_human
  status=ok 的 reason="routed"。
"""

import json
import os
import time
from typing import Dict, List, Optional

_QUEUE = "goldset/triage_queue.json"
REASONS = ("needs_write", "low_confidence", "unverified", "suspicious", "needs_human", "review_hold")
_GOOD = ("ok", "resolved")


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
    if not isinstance(signal, dict):
        return None
    return {k: signal[k] for k in ("clean", "confidence", "anchored", "anchor", "diff_pct", "verify", "committed")
            if k in signal}


def _upsert(code, year, field, reason, status, signal=None, note="") -> Dict:
    """按 (code,year,field) 去重的 upsert。"""
    recs = _load()
    for r in recs:
        if _key(r) == (code, year, field):
            r.update(reason=reason, status=status, signal=_sig(signal), note=note,
                     updated_at=_now())
            _save(recs)
            return r
    rec = {"code": code, "year": year, "field": field, "reason": reason,
           "status": status, "signal": _sig(signal), "note": note,
           "created_at": _now(), "updated_at": _now()}
    recs.append(rec)
    _save(recs)
    return rec


def enqueue(code, year, field, reason, signal=None, note="") -> Dict:
    """登记一条待办(status=open)。"""
    return _upsert(code, year, field, reason, "open", signal, note)


def record_ok(code, year, field, signal=None) -> Dict:
    """登记一条可信记录(status=ok：已认证 routed + 硬规则过)。"""
    return _upsert(code, year, field, "routed", "ok", signal)


def resolve(code, year, field) -> bool:
    """销账：该(报告,字段)已被解析器搞定(如认证后)。原问题→resolved(算可信)。"""
    recs = _load()
    changed = False
    for r in recs:
        if _key(r) == (code, year, field) and r["status"] != "resolved":
            r["status"], r["updated_at"] = "resolved", _now()
            changed = True
    if changed:
        _save(recs)
    return changed


def set_status(code, year, field, status) -> bool:
    recs = _load()
    for r in recs:
        if _key(r) == (code, year, field):
            r["status"], r["updated_at"] = status, _now()
            _save(recs)
            return True
    return False


def list_open(reason: str = None, field: str = None) -> List[Dict]:
    """要干活的(红/橙)。"""
    return [r for r in _load() if r["status"] == "open"
            and (reason is None or r["reason"] == reason)
            and (field is None or r["field"] == field)]


def list_ok(field: str = None) -> List[Dict]:
    """可信的(绿)：status ∈ ok/resolved。"""
    return [r for r in _load() if r["status"] in _GOOD
            and (field is None or r["field"] == field)]


def summary() -> Dict:
    """覆盖率汇总：可信(绿) vs 要干活(红橙) + 覆盖率%。给控制台那个'安全感大数字'。"""
    recs = _load()
    by_status, by_reason, by_field_ok = {}, {}, {}
    for r in recs:
        s = r["status"]
        by_status[s] = by_status.get(s, 0) + 1
        if s == "open":
            by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        elif s in _GOOD:
            by_field_ok[r["field"]] = by_field_ok.get(r["field"], 0) + 1
    total = len(recs)
    good = by_status.get("ok", 0) + by_status.get("resolved", 0)          # 绿:已核验
    needs_write_n = by_reason.get("needs_write", 0)
    parsed = total - needs_write_n                                        # 绿+黄+橙:解析出数据
    pct = lambda n: round(n / total * 100, 1) if total else 0.0
    return {"total": total,
            "verified": good, "verified_pct": pct(good),                 # 真可信(锚验证)
            "parsed": parsed, "parsed_pct": pct(parsed),                 # 解出数据(含待核验)
            "ok": good, "open": by_status.get("open", 0),
            "coverage_pct": pct(good),
            "by_status": by_status, "by_reason": by_reason, "ok_by_field": by_field_ok}


def triage_report(code: str, year: int, fields: List[str] = None) -> List[Dict]:
    """对一份**已抽表缓存**的报告分诊：逐字段读**单一真源** → 落台账。
    needs_repair→needs_write(红)；routed 低置信→low_confidence(橙)；routed 高/无锚→ok(绿)。
    状态/信号/值全部取自 canonical（与审核台/裁判/置信度同一份），不再独立重算。"""
    from src.eval.canonical import get_canonical
    from src.eval.field_spec import FIELDS
    canon = get_canonical(code, year)
    if canon is None:
        return []
    out = []
    for fname, spec in FIELDS.items():
        if fields and fname not in fields:
            continue
        rec = canon.get(fname) or {}
        sig = rec.get("signal") or {}
        if rec.get("status") == "needs_repair":
            out.append(enqueue(code, year, fname, "needs_write", sig))      # 红:没解析器
        elif rec.get("status") == "routed":
            conf = sig.get("confidence")
            if conf == "high":                       # 锚过(某维度和≈DB锚)不再直接算可信 →
                # 交复核 agent 审锚的盲区(其他维度/摘行/重复/名称/占比)。它点头才绿灯入库。
                out.append(_verify_green(code, year, fname, spec, rec, sig))
            elif conf == "low":                      # 橙:锚对不上 → 可疑
                out.append(enqueue(code, year, fname, "low_confidence", sig))
            else:                                    # 黄:路由过硬规则但无DB锚可验 → 待核验
                out.append(enqueue(code, year, fname, "unverified", sig))
    return out


def _verify_green(code: str, year: int, field: str, spec, rec: Dict, sig: Dict) -> Dict:
    """锚过的绿灯 → 复核 agent。pass→record_ok(真绿)；hold→enqueue(review_hold,送人审)；
    unknown(无源文可对照)→保持绿但标注(不因缺证据误杀)。复核 agent 挂了也不阻断,退回旧绿。"""
    from src.eval.field_spec import get_spec
    value = rec.get("value")                            # 单一真源值(=审核台/裁判所见)
    try:
        from src.agents.llm_judge import verify_field
        v = verify_field(field, code, year, value, sig=sig, spec=spec or get_spec(field))
    except Exception as e:
        return record_ok(code, year, field, {**(sig or {}), "verify": f"error:{e}"})
    verdict = v.get("verdict")
    if verdict == "hold":                               # 复核挑出实锤疑点 → 打回人审
        return enqueue(code, year, field, "review_hold", sig, note=v.get("summary", ""))
    # pass → 真绿:复核 agent(LLM逐项对照源文)点头 = 终审通过 → 自动入库(测试库,留痕)
    # unknown(无源文可对照) → 保持绿但不入库(未经LLM确认,不因缺证据既不放行也不误入库)
    committed = None
    if verdict == "pass":
        try:
            committed = _auto_commit(code, year, field, value, sig)
        except Exception as e:
            committed = f"error:{str(e)[:60]}"
    return record_ok(code, year, field, {**(sig or {}), "verify": verdict, "committed": committed})


def _auto_commit(code: str, year: int, field: str, value, sig: Dict):
    """复核 pass → 入库。走现有 commit 机制:enqueue_commit(留痕 source=verify_agent) → commit_approve
    (写 REPORTS_TABLE 测试库)。Config.AUTO_COMMIT_ON_VERIFY=False 时只入队 pending,等 ⑤ 人审。
    返回状态串写进台账 signal.committed。"""
    from src.eval.test_store import enqueue_commit
    from src.config import Config
    rid = enqueue_commit(code, year, field, value, confidence=(sig or {}).get("confidence"), source="verify_agent")
    if not Config.AUTO_COMMIT_ON_VERIFY:
        return f"pending#{rid}"                          # 保留人审:只入队,不自动写库
    from src.console_service import commit_approve
    r = commit_approve(rid, note="复核agent自动通过")
    return "committed" if r.get("ok") else f"commit_err:{str(r.get('error'))[:50]}"


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
