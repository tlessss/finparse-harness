"""测试阶段数据存储 — SQLite。

每次测试(选表 select / 路由 route / 解析 parse)的快照存这里，供人工回看 + 标人工判定(对/错)。
按 (stage, stock_code, year, field) **upsert**：同一项重测只更新自动字段，**保留人工 verdict/note**。
位置：goldset/test_store.db（自包含，无需起 DB 服务）。
"""

import json
import os
import sqlite3
import time

_DB = "goldset/test_store.db"


def _conn():
    os.makedirs(os.path.dirname(_DB) or ".", exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS test_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, stage TEXT, stock_code TEXT, year INTEGER, field TEXT,
        status TEXT, confidence TEXT, verdict TEXT, summary TEXT, payload TEXT, note TEXT,
        UNIQUE(stage, stock_code, year, field))""")
    return c


def save_test(stage, code, year, field, status=None, confidence=None,
              summary=None, payload=None):
    """存/更新一条测试记录(自动字段)。保留已有的人工 verdict/note。返回行 id。"""
    c = _conn()
    c.execute(
        """INSERT INTO test_runs(created_at,stage,stock_code,year,field,status,confidence,summary,payload)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(stage,stock_code,year,field) DO UPDATE SET
             created_at=excluded.created_at, status=excluded.status,
             confidence=excluded.confidence, summary=excluded.summary, payload=excluded.payload""",
        (time.strftime("%Y-%m-%d %H:%M:%S"), stage, code, int(year), field, status, confidence,
         json.dumps(summary, ensure_ascii=False) if summary is not None else None,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None))
    c.commit()
    rid = c.execute("SELECT id FROM test_runs WHERE stage=? AND stock_code=? AND year=? AND field=?",
                    (stage, code, int(year), field)).fetchone()["id"]
    c.close()
    return rid


def list_tests(stage=None, code=None, field=None, verdict=None, limit=500):
    c = _conn()
    q = ("SELECT id,created_at,stage,stock_code,year,field,status,confidence,verdict,summary,note "
         "FROM test_runs WHERE 1=1")
    args = []
    for col, val in (("stage", stage), ("stock_code", code), ("field", field), ("verdict", verdict)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in c.execute(q, args).fetchall()]
    c.close()
    for r in rows:
        if r.get("summary"):
            try:
                r["summary"] = json.loads(r["summary"])
            except Exception:
                pass
    return rows


def get_test(rid):
    c = _conn()
    r = c.execute("SELECT * FROM test_runs WHERE id=?", (rid,)).fetchone()
    c.close()
    if not r:
        return None
    d = dict(r)
    for k in ("summary", "payload"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def set_verdict(rid, verdict, note=""):
    """人工标判定(ok/wrong/...)。"""
    c = _conn()
    c.execute("UPDATE test_runs SET verdict=?, note=? WHERE id=?", (verdict, note, rid))
    c.commit()
    c.close()
    return True


def stats():
    """按 阶段×判定 汇总，给个总览。"""
    c = _conn()
    rows = [dict(r) for r in c.execute(
        "SELECT stage, COALESCE(verdict,'未标') v, COUNT(*) n FROM test_runs GROUP BY stage, v").fetchall()]
    total = c.execute("SELECT COUNT(*) n FROM test_runs").fetchone()["n"]
    c.close()
    return {"total": total, "by_stage_verdict": rows}
