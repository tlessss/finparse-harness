"""测试阶段数据存储 — SQLite。

每次测试(选表 select / 路由 route / 解析 parse)的快照存这里，供人工回看 + 标人工判定(对/错)。
按 (stage, stock_code, year, field) **upsert**：同一项重测只更新自动字段，**保留人工 verdict/note**。
位置：goldset/test_store.db（自包含，无需起 DB 服务）。
"""

import json
import os
import sqlite3
import time
import uuid

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
    # LLM 判定对话台:每次发送(可能被人编辑过的 messages)+ LLM 回复,全留痕
    c.execute("""CREATE TABLE IF NOT EXISTS judge_chats(
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,
        stock_code TEXT, year INTEGER, field TEXT, messages TEXT, reply TEXT)""")
    # 入库审核队列:LLM判ok→进这里(pending,浅绿)→人通过(approved,入库)/驳回(rejected)
    c.execute("""CREATE TABLE IF NOT EXISTS review_commits(
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, reviewed_at TEXT,
        stock_code TEXT, year INTEGER, field TEXT, result TEXT, confidence TEXT,
        source TEXT, status TEXT, note TEXT)""")
    # 流水线血缘(append-only)：一次跑一行，存整条链路 + 结局。latest-wins(取 MAX(id))。
    c.execute("""CREATE TABLE IF NOT EXISTS pipeline_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,
        stock_code TEXT, year INTEGER, field TEXT,
        outcome TEXT, via TEXT, reason TEXT, chain TEXT, verify TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_pr_key ON pipeline_runs(stock_code,year,field,id)")
    c.execute("""CREATE TABLE IF NOT EXISTS process_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT, created_at TEXT,
        stock_code TEXT, year INTEGER, field TEXT,
        agent_id TEXT, event_type TEXT, outcome TEXT,
        payload_json TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_pe_run ON process_events(run_id,id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_pe_key ON process_events(stock_code,year,field,id DESC)")
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


def save_chat(code, year, field, messages, reply):
    """记一条 LLM 判定对话(发送的 messages + 回复)。返回行 id。"""
    c = _conn()
    cur = c.execute(
        "INSERT INTO judge_chats(created_at,stock_code,year,field,messages,reply) VALUES(?,?,?,?,?,?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), code, int(year), field,
         json.dumps(messages, ensure_ascii=False), reply))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def enqueue_commit(code, year, field, result, confidence=None, source="llm_ok"):
    """LLM判ok → 进入库审核队列(pending)。同(code,年,字段)**全状态幂等**:重新入库先删旧记录
    (含已 approved/rejected)再插新的,台账每份财报只留最新一条(和真库 UPDATE 覆盖单行一致)。返回 id。"""
    c = _conn()
    c.execute("DELETE FROM review_commits WHERE stock_code=? AND year=? AND field=?",
              (code, int(year), field))
    cur = c.execute(
        "INSERT INTO review_commits(created_at,stock_code,year,field,result,confidence,source,status) "
        "VALUES(?,?,?,?,?,?,?,'pending')",
        (time.strftime("%Y-%m-%d %H:%M:%S"), code, int(year), field,
         json.dumps(result, ensure_ascii=False), str(confidence), source))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def list_commits(status="pending", limit=300):
    c = _conn()
    q = "SELECT id,created_at,reviewed_at,stock_code,year,field,result,confidence,source,status,note FROM review_commits"
    args = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in c.execute(q, args).fetchall()]
    c.close()
    for r in rows:
        try:
            r["result"] = json.loads(r["result"]) if r.get("result") else None
        except Exception:
            pass
    return rows


def get_commit(rid):
    """返回记录;result_json 保留原始字符串(给入库写库用),result 为解析后(给展示用)。"""
    c = _conn()
    r = c.execute("SELECT * FROM review_commits WHERE id=?", (rid,)).fetchone()
    c.close()
    if not r:
        return None
    d = dict(r)
    d["result_json"] = d.get("result")
    try:
        d["result"] = json.loads(d["result"]) if d.get("result") else None
    except Exception:
        pass
    return d


def set_commit_status(rid, status, note=""):
    c = _conn()
    c.execute("UPDATE review_commits SET status=?, note=?, reviewed_at=? WHERE id=?",
              (status, note, time.strftime("%Y-%m-%d %H:%M:%S"), rid))
    c.commit()
    c.close()
    return True


def list_chats(code=None, field=None, limit=200):
    c = _conn()
    q = "SELECT id,created_at,stock_code,year,field,messages,reply FROM judge_chats WHERE 1=1"
    args = []
    for col, val in (("stock_code", code), ("field", field)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in c.execute(q, args).fetchall()]
    c.close()
    for r in rows:
        try:
            r["messages"] = json.loads(r["messages"]) if r.get("messages") else []
        except Exception:
            r["messages"] = []
    return rows


# ── 流水线血缘 pipeline_runs ──

def save_run(code, year, field, outcome, via=None, reason=None, chain=None, verify=None):
    """存一次流水线跑批(append-only)。chain/verify 存 JSON。返回行 id。"""
    c = _conn()
    c.execute(
        """INSERT INTO pipeline_runs(created_at,stock_code,year,field,outcome,via,reason,chain,verify)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (time.strftime("%Y-%m-%d %H:%M:%S"), code, int(year), field, outcome, via, reason,
         json.dumps(chain, ensure_ascii=False) if chain is not None else None,
         json.dumps(verify, ensure_ascii=False) if verify is not None else None))
    c.commit()
    rid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    c.close()
    return rid


def _parse_run(r):
    d = dict(r)
    for k in ("chain", "verify"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except Exception:
            d[k] = None
    return d


def get_latest_run(code, year, field):
    """该 (code,year,field) 最近一次跑批(取 MAX(id))。"""
    c = _conn()
    r = c.execute(
        "SELECT * FROM pipeline_runs WHERE stock_code=? AND year=? AND field=? ORDER BY id DESC LIMIT 1",
        (code, int(year), field)).fetchone()
    c.close()
    return _parse_run(r) if r else None


def list_latest_runs(year, fields=None):
    """每个 (code,field) 的最近一次跑批。fields 传入则只取这些字段。"""
    c = _conn()
    rows = c.execute(
        """SELECT p.* FROM pipeline_runs p
           JOIN (SELECT stock_code,field,MAX(id) mid FROM pipeline_runs WHERE year=? GROUP BY stock_code,field) m
           ON p.id=m.mid ORDER BY p.stock_code""", (int(year),)).fetchall()
    c.close()
    out = [_parse_run(r) for r in rows]
    if fields:
        out = [d for d in out if d["field"] in fields]
    return out


def new_run_id() -> str:
    """生成一次流程运行的 run_id。"""
    return str(uuid.uuid4())


def emit_event(run_id, code, year, field, agent_id, event_type, outcome=None, payload=None):
    """追加一条流程事件。payload 会存为 JSON。"""
    c = _conn()
    c.execute(
        """INSERT INTO process_events(run_id,created_at,stock_code,year,field,agent_id,event_type,outcome,payload_json)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (run_id, time.strftime("%Y-%m-%d %H:%M:%S"), code, int(year), field, agent_id, event_type, outcome,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None))
    c.commit()
    rid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    c.close()
    return rid


def list_events(code, year, field, run_id=None, limit=200):
    """按 run_id（优先）或按最近记录查询事件时间线。"""
    c = _conn()
    if run_id:
        rows = c.execute(
            """SELECT * FROM process_events
               WHERE run_id=? AND stock_code=? AND year=? AND field=?
               ORDER BY id ASC LIMIT ?""",
            (run_id, code, int(year), field, int(limit))).fetchall()
    else:
        latest = c.execute(
            """SELECT run_id FROM process_events
               WHERE stock_code=? AND year=? AND field=?
               ORDER BY id DESC LIMIT 1""",
            (code, int(year), field)).fetchone()
        if not latest:
            c.close()
            return []
        rows = c.execute(
            """SELECT * FROM process_events
               WHERE run_id=? AND stock_code=? AND year=? AND field=?
               ORDER BY id ASC LIMIT ?""",
            (latest["run_id"], code, int(year), field, int(limit))).fetchall()
    c.close()
    out = [dict(r) for r in rows]
    for r in out:
        if r.get("payload_json"):
            try:
                r["payload"] = json.loads(r["payload_json"])
            except Exception:
                r["payload"] = None
        else:
            r["payload"] = None
    return out
