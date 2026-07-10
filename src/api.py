"""
FinParseAI API 服务

启动:
  python3 -m src.api
  或: uvicorn src.api:app --host 0.0.0.0 --port 8200 --reload

端点:
  GET  /health              — 健康检查
  POST /parse               — 解析一份 PDF（上传文件）
  POST /parse/by-code       — 按股票代码+年份从PDF缓存解析
  GET  /status              — 运行状态概览
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

import json
import time
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import Config
from src.database import find_stock, get_conn, list_reports
from src.engine_orchestrator import FinParseAI

app = FastAPI(title="FinParseAI", version="0.1.0", docs_url="/docs")

# CORS — 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局引擎（单例）
_engine: Optional[FinParseAI] = None


def get_engine() -> FinParseAI:
    global _engine
    if _engine is None:
        _engine = FinParseAI()
    return _engine


# ── 状态 ──
_running_tasks = {}


# ── 请求模型 ──

class ParseByCodeRequest(BaseModel):
    stock_code: str
    year: int
    db_write: bool = True


class BatchParseRequest(BaseModel):
    parser_type: str = ""  # 预定字段
    limit: int = 0
    db_write: bool = True


# ── 端点 ──

@app.get("/health")
def health():
    from src.database import reports_table
    tbl = reports_table()
    return {"status": "ok", "service": "FinParseAI", "version": "0.1.0",
            "reports_table": tbl, "is_test": tbl != "financial_reports"}


@app.get("/status")
def status():
    """运行状态概览"""
    total = 0
    parsed = 0
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM financial_reports WHERE data_source IN ('hybrid','pdf')")
            total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM financial_reports WHERE revenue_breakdown IS NOT NULL")
            parsed = cur.fetchone()["c"]
        conn.close()
    except Exception:
        pass

    return {
        "status": "running" if _running_tasks else "idle",
        "tasks_running": len(_running_tasks),
        "db_pdf_records": total,
        "db_parsed_fields": parsed,
        "config": {
            "pdf_cache_dir": str(Config.PDF_CACHE_DIR),
            "rag_data_dir": str(Config.RAG_DATA_DIR),
            "port": Config.PORT,
        },
    }


@app.post("/parse")
async def parse_upload(file: UploadFile = File(...), db_write: bool = Form(True)):
    """上传 PDF 文件并解析"""
    # 保存临时文件
    tmp_dir = Path("/tmp/finparseai")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / file.filename
    with open(tmp_path, "wb") as f:
        f.write(await file.read())

    try:
        engine = get_engine()
        result = engine.run(str(tmp_path), db_write=db_write)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/parse/by-code")
def parse_by_code(req: ParseByCodeRequest):
    """从 PDF 缓存按股票代码+年份解析"""
    # 查找缓存中的 PDF
    cache_dir = Config.PDF_CACHE_DIR
    pdf_path = None
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if f.suffix != ".pdf":
                continue
            parts = f.stem.split("_")
            if len(parts) >= 2 and parts[0] == req.stock_code and parts[1] == str(req.year):
                pdf_path = str(f)
                break

    if not pdf_path:
        raise HTTPException(status_code=404, detail=f"PDF 缓存未找到: {req.stock_code}_{req.year}.pdf")

    # 获取公司名称
    stock = find_stock(req.stock_code)
    company_name = stock["name"] if stock else None

    engine = get_engine()
    result = engine.run(pdf_path, stock_code=req.stock_code, report_year=req.year,
                        company_name=company_name, db_write=req.db_write)
    return result


@app.get("/results")
def list_parse_results(limit: int = 20):
    """查看最新的解析结果"""
    try:
        reports = list_reports(limit=limit)
        result = []
        for r in reports:
            result.append({
                "id": r["id"],
                "stock_code": r["stock_code"],
                "company_name": r["company_name"],
                "report_year": r["report_year"],
                "data_source": r.get("data_source"),
                "has_revenue_breakdown": r.get("revenue_breakdown") is not None,
                "has_cost_breakdown": r.get("cost_breakdown") is not None,
                "has_employees": r.get("employees") is not None,
                "has_rnd_info": r.get("rnd_info") is not None,
                "has_top_clients": r.get("top_clients") is not None,
                "has_top_suppliers": r.get("top_suppliers") is not None,
                "pdf_parsed_at": str(r.get("pdf_parsed_at") or ""),
                "quality_score": r.get("quality_score"),
            })
        return {"records": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/validate")
def validate_report(stock_code: str = None, report_year: int = None):
    """
    对指定的解析结果执行向量校验。

    如果未指定 stock_code/report_year，则从数据库中读取最近一条 hybrid 记录进行校验。
    """
    from src.validators.vector_validator import VectorValidator

    # 获取解析结果
    parse_result = None
    report_id = None

    if stock_code and report_year:
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM financial_reports WHERE stock_code=%s AND report_year=%s AND report_quarter='annual' LIMIT 1",
                    (stock_code, report_year),
                )
                row = cur.fetchone()
                if row:
                    report_id = row["id"]
                    # 组装 parse_result
                    parse_result = {
                        "stock_code": row["stock_code"],
                        "company_name": row["company_name"],
                        "report_year": row["report_year"],
                    }
                    for f in ["revenue_breakdown", "rnd_info", "employees",
                              "cost_breakdown", "top_clients", "top_suppliers"]:
                        val = row.get(f)
                        if val:
                            # MySQL JSON 字段返回 str，需解析
                            if isinstance(val, str):
                                try:
                                    val = json.loads(val)
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            parse_result[f] = val
                    parse_result["field_count"] = sum(1 for f in [
                        "revenue_breakdown", "rnd_info", "employees",
                        "cost_breakdown", "top_clients", "top_suppliers"
                    ] if parse_result.get(f))
            conn.close()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=400, detail="需指定 stock_code 和 report_year")

    if not parse_result:
        raise HTTPException(status_code=404, detail="未找到解析记录")

    try:
        validator = VectorValidator()
        report = validator.validate(parse_result)
        return {
            "report_id": report_id,
            "stock_code": stock_code,
            "report_year": report_year,
            "validation": report,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/iterate")
def run_iteration(stock_code: str, year: int):
    """已废弃：旧 IterationEngine 已迁入 archive/legacy。请用 POST /pipeline/run_llm。"""
    raise HTTPException(
        status_code=410,
        detail="POST /iterate 已废弃(见 archive/legacy/iteration.py)。请用 POST /pipeline/run_llm",
    )


def _deprecated_410(detail: str):
    raise HTTPException(status_code=410, detail=detail)


# ── 已废弃端点（PR3 迁入 archive，前端未使用）──

@app.get("/review/tasks")
def list_review_tasks(limit: int = 20, pending_only: bool = False):
    _deprecated_410("旧 ReviewManager 已迁入 archive/legacy/review。请用 GET /review/task?code=")


@app.get("/review/task/{task_id}")
def get_review_task(task_id: int):
    _deprecated_410("旧 ReviewManager 已迁入 archive/legacy/review")


@app.post("/review/{task_id}/start")
def start_review(task_id: int, reviewer: str = "admin"):
    _deprecated_410("旧 ReviewManager 已迁入 archive/legacy/review")


@app.post("/review/{task_id}/approve")
def approve_review(task_id: int, comment: str = ""):
    _deprecated_410("旧 ReviewManager 已迁入 archive/legacy/review")


@app.post("/review/{task_id}/reject")
def reject_review(task_id: int, comment: str = ""):
    _deprecated_410("旧 ReviewManager 已迁入 archive/legacy/review")


@app.post("/review/{task_id}/fix")
def apply_fix(task_id: int, fixes: dict, comment: str = ""):
    _deprecated_410("旧 ReviewManager 已迁入 archive/legacy/review")


@app.get("/export/json")
def export_parse_json(stock_code: str = None, year: int = None, limit: int = 10):
    _deprecated_410("export 已迁入 archive/legacy/export")


@app.get("/export/csv")
def export_parse_csv(stock_code: str = None, year: int = None, limit: int = 100):
    _deprecated_410("export 已迁入 archive/legacy/export")


@app.get("/export/versions")
def export_parser_versions(limit: int = 20):
    _deprecated_410("export 已迁入 archive/legacy/export")


# ── 控制台审核台（活跃）──

class RecodeRequest(BaseModel):
    stock_code: str
    year: int = 2025
    code: str


@app.post("/control/{action}")
def console_control(action: str):
    """已废弃：请用 POST /batch/control/{action}。"""
    _deprecated_410("POST /control 已废弃，请用 POST /batch/control")


@app.get("/heal/records")
def console_heal_records():
    _deprecated_410("GET /heal/records 已废弃，请用 /pipeline/result 或 /pipeline/failures")


@app.post("/review/recode")
def console_recode(req: RecodeRequest):
    """人改解析器代码 → 重过闸 → 返回 {score, exact, mismatches}。"""
    from src.console_service import recode
    return recode(req.stock_code, req.year, req.code)


@app.get("/review/task")
def console_review_task(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """审核任务：指定字段的 结果 + 溯源(page/bbox) + 渲染页(base64) + 解析器源码。"""
    from src.console_service import review_task
    return review_task(stock_code, year, field)


@app.get("/debug/select")
def debug_select(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    _deprecated_410("GET /debug/select 已废弃，请用 GET /debug/recall")


@app.get("/debug/page")
def debug_page(stock_code: str, year: int = 2025, page: int = 1):
    """渲染某页为图片（选表调试台"看PDF原页"）。"""
    from src.console_service import render_page
    return render_page(stock_code, year, page)


@app.get("/debug/route")
def debug_route(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """路由测试台：指纹/命中认证解析器/路由结果/试过的候选/过锚。"""
    from src.console_service import route_debug
    return route_debug(stock_code, year, field)


@app.get("/debug/parse")
def debug_parse(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """冷启动解析测试台：强制跑通用解析器(绕过路由)→各维度对锚。"""
    from src.console_service import parse_debug
    return parse_debug(stock_code, year, field)


@app.get("/debug/judge")
def debug_judge(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """LLM判定测试台：解析→LLM对照溯源原表逐项判对错(慢,~10-20s)。"""
    from src.console_service import judge_debug
    return judge_debug(stock_code, year, field)


class JudgeChatRequest(BaseModel):
    code: str
    year: int = 2025
    field: str = "revenue_breakdown"
    messages: list


@app.get("/debug/heal")
def debug_heal(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """自愈测试台(真失败筛子)：判这份要不要自愈 + 病历/修复方向。"""
    from src.console_service import heal_debug
    return heal_debug(stock_code, year, field)


@app.get("/debug/heal/prepare")
def debug_heal_prepare(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """自愈对话台：拼调试包(病历+原表+错值+配置+代码)给AI,返回可编辑messages。"""
    from src.console_service import heal_prepare
    return heal_prepare(stock_code, year, field)


@app.post("/debug/heal/chat")
def debug_heal_chat(req: JudgeChatRequest):
    """自愈对话：发送messages给AI,记录,返回修复建议。"""
    from src.console_service import heal_chat
    return heal_chat(req.code, req.year, req.field, req.messages)


class AddMarkerRequest(BaseModel):
    text: str
    dim: str
    field: str = "revenue"


@app.post("/tool/add_section_marker")
def tool_add_section_marker(req: AddMarkerRequest):
    """规则工具：往 revenue.yaml dimensions 加切桶标记 text→dim（校验/幂等/冲突安全）。"""
    from src.agents.rule_tools import add_section_marker
    return add_section_marker(req.text, req.dim, req.field)


class ApplyFixRequest(BaseModel):
    code: str
    year: int = 2025
    field: str = "revenue_breakdown"
    fix: dict


@app.post("/tool/apply_fix")
def tool_apply_fix(req: ApplyFixRequest):
    """应用 AI 的结构化修复 + 回链重测，返回修复前后对照。"""
    from src.console_service import apply_fix
    return apply_fix(req.code, req.year, req.field, req.fix)


@app.get("/debug/columns")
def debug_columns(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """认列测试台：选中表怎么判 名称/金额/占比列。"""
    from src.console_service import columns_debug
    return columns_debug(stock_code, year, field)


@app.get("/debug/recall")
def debug_recall(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """向量召回测试台：语义相似度召回候选表(选表解耦第一段)。"""
    from src.console_service import recall_debug
    return recall_debug(stock_code, year, field)


@app.get("/debug/judge/prepare")
def debug_judge_prepare(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """对话台：拼好发给LLM的messages但不发送,返给前端编辑。"""
    from src.console_service import judge_prepare
    return judge_prepare(stock_code, year, field)


@app.post("/debug/judge/chat")
def debug_judge_chat(req: JudgeChatRequest):
    """对话台：把(可编辑过的)messages发给LLM,记录,返回回复。"""
    from src.console_service import judge_chat
    return judge_chat(req.code, req.year, req.field, req.messages)


@app.get("/debug/judge/chats")
def debug_judge_chats(code: str = None, field: str = None, limit: int = 200):
    """对话台：列出记录下来的历史对话。"""
    from src.eval.test_store import list_chats
    return {"chats": list_chats(code, field, limit)}


@app.get("/debug/rule_code/prepare")
def debug_rule_code_prepare(stock_code: str, year: int = 2025, field: str = "revenue_breakdown",
                            decision: str = "", root_cause: str = "", next_action: str = "", summary: str = ""):
    """第二阶段：规则/代码诊断调试包。可接收第一阶段结论作为上下文。"""
    from src.console_service import rule_code_prepare
    stage1 = {
        "decision": decision,
        "root_cause": root_cause,
        "next_action": next_action,
        "summary": summary,
    }
    return rule_code_prepare(stock_code, year, field, stage1=stage1)


# ── 复核 agent 对话台（绿灯专用：审锚的盲区，pass 才真过）──

@app.get("/debug/verify/prepare")
def debug_verify_prepare(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """复核对话台：拼好发给复核 agent 的 messages 但不发送,返给前端编辑。"""
    from src.console_service import verify_prepare
    return verify_prepare(stock_code, year, field)


@app.post("/debug/verify/chat")
def debug_verify_chat(req: JudgeChatRequest):
    """复核对话：把(可编辑过的)messages 发给复核 agent,记录,返回 pass/hold。"""
    from src.console_service import verify_chat
    return verify_chat(req.code, req.year, req.field, req.messages)


@app.get("/debug/verify/chats")
def debug_verify_chats(code: str = None, field: str = None, limit: int = 200):
    """复核对话台：列出该字段的复核历史(与 judge 分开,tag=field::verify)。"""
    from src.eval.test_store import list_chats
    return {"chats": list_chats(code, f"{field}::verify" if field else None, limit)}


# ── 入库审核队列(LLM判ok→人审→入库) ──

@app.get("/commit/list")
def commit_list(status: str = "pending", limit: int = 300):
    """入库审核队列(默认 pending=待人审的浅绿项)。"""
    from src.eval.test_store import list_commits
    return {"commits": list_commits(status, limit)}


class CommitActionRequest(BaseModel):
    id: int
    note: str = ""


@app.post("/commit/approve")
def commit_approve_api(req: CommitActionRequest):
    """人审通过 → 写进生产库 financial_reports。"""
    from src.console_service import commit_approve
    return commit_approve(req.id, req.note)


@app.post("/commit/reject")
def commit_reject_api(req: CommitActionRequest):
    """人审驳回 → 不入库。"""
    from src.console_service import commit_reject
    return commit_reject(req.id, req.note)


# ── 测试阶段数据库(SQLite) ──

@app.get("/test/list")
def test_list(stage: str = None, code: str = None, field: str = None, verdict: str = None, limit: int = 500):
    """列出测试库记录(可按 阶段/报告/字段/判定 筛)。"""
    from src.eval.test_store import list_tests
    return {"records": list_tests(stage, code, field, verdict, limit)}


@app.get("/test/stats")
def test_stats():
    """测试库总览(按 阶段×判定 汇总)。"""
    from src.eval.test_store import stats
    return stats()


class VerdictRequest(BaseModel):
    id: int
    verdict: str            # ok / wrong / ...
    note: str = ""


@app.post("/test/verdict")
def test_verdict(req: VerdictRequest):
    """人工给某条测试标判定(对/错)。"""
    from src.eval.test_store import set_verdict
    set_verdict(req.id, req.verdict, req.note)
    return {"ok": True}


@app.get("/test/{rid}")
def test_get(rid: int):
    """取一条测试记录的完整快照。"""
    from src.eval.test_store import get_test
    return get_test(rid) or {"error": "不存在"}


class GoldenRequest(BaseModel):
    stock_code: str
    year: int = 2025
    revenue_breakdown: dict
    note: str = ""


@app.post("/review/golden")
def console_save_golden(req: GoldenRequest):
    """人确认的结果 → 存为 golden（认证解析器的真值依据）。"""
    from src.console_service import save_golden
    return save_golden(req.stock_code, req.year, req.revenue_breakdown, note=req.note)


@app.post("/review/certify")
def console_certify(req: RecodeRequest):
    """人审通过的解析器 → 服务端重验 exact → 写版本文件 + 入认证目录 → 下次同版式免审。"""
    from src.console_service import certify_parser
    return certify_parser(req.stock_code, req.year, req.code)


# ── 判定层 + 分诊队列接口（前端控制台用）──

class TriageScanRequest(BaseModel):
    codes: list
    year: int = 2025


class TriageReviewRequest(BaseModel):
    reason: str = "low_confidence"
    limit: int = 20


class JudgeRequest(BaseModel):
    stock_code: str
    year: int = 2025
    field: str


def _ensure_cached(code, year):
    """确保该报告的表已在缓存(没有就解析一次)，否则路由/裁判没表可用。"""
    from src.eval.table_cache import get_tables
    if get_tables(code, year) is None:
        try:
            from src.console_service import _cached_engine_parse
            _cached_engine_parse(code, year)
        except Exception:
            pass


@app.get("/triage/summary")
def triage_summary():
    """分诊队列汇总：open 总数 + 按 reason/字段 分布。"""
    from src.eval.triage_queue import summary
    return summary()


@app.get("/triage/queue")
def triage_queue_list(reason: str = None, field: str = None, status: str = "open"):
    """覆盖台账。status=open(待办,默认) | ok(可信绿) | all(全部)。reason/field 可选筛选。"""
    from src.eval.triage_queue import list_open, list_ok, _load
    if status == "ok":
        return {"records": list_ok(field=field)}
    if status == "all":
        return {"records": _load()}
    return {"records": list_open(reason=reason, field=field)}


@app.post("/triage/scan")
def triage_scan(req: TriageScanRequest):
    """对一批报告分诊、落盘。未抽表的先解析一次缓存。"""
    from src.eval.triage_queue import triage_report
    out = []
    for code in req.codes:
        _ensure_cached(code, req.year)
        out += triage_report(code, req.year)
    return {"enqueued": out}


@app.post("/triage/review")
def triage_review(req: TriageReviewRequest):
    """对队列里某 reason 跑 LLM 复核：ok 自动销账 / suspicious 改标（较慢）。"""
    from src.agents.llm_judge import review_queue
    return {"reviewed": review_queue(reason=req.reason, limit=req.limit)}


@app.post("/review/judge")
def review_judge(req: JudgeRequest):
    """对某(报告,字段)跑 #2 溯源 LLM 裁判（按需触发，较慢 ~10-20s）。"""
    from src.eval.field_spec import FIELDS, get_spec
    from src.agents.llm_judge import judge_field
    from src.eval.canonical import get_field          # ★ 单一真源:判的值 = 审核台显示的值
    if req.field not in FIELDS:
        return {"verdict": "unknown", "summary": f"未知字段 {req.field}", "field": req.field}
    _ensure_cached(req.stock_code, req.year)
    spec = get_spec(req.field)
    rec = get_field(req.stock_code, req.year, req.field)
    value = rec.get("value") if rec else None
    if value is None:
        return {"verdict": "unknown", "summary": "该字段无解析结果，无法裁判", "field": req.field}
    return judge_field(req.field, req.stock_code, req.year, value,
                       provenance=rec.get("provenance"), spec=spec)


@app.get("/review/signals")
def review_signals(stock_code: str, year: int = 2025):
    """每字段的置信度信号(#1跨表锚) + DB锚值，供审核台展示徽章。"""
    from src.eval.field_spec import FIELDS
    from src.eval.anchors import get_anchors
    from src.eval.canonical import get_canonical      # ★ 单一真源:徽章基于审核台显示的那份值
    _ensure_cached(stock_code, year)
    canon = get_canonical(stock_code, year) or {}
    out = {}
    for field in FIELDS:
        rec = canon.get(field) or {}
        sig = rec.get("signal") or {}
        out[field] = {"status": rec.get("status"), "clean": sig.get("clean"),
                      "confidence": sig.get("confidence"), "anchored": sig.get("anchored"),
                      "anchor": sig.get("anchor")}
    return {"signals": out, "anchors": get_anchors(stock_code, year)}


# ── 批量跑批器接口 ──

class BatchStartRequest(BaseModel):
    codes: list
    year: int = 2025
    db_write: bool = False
    heal: bool = False          # 完整流程：失败字段自动走 LLM 自愈(慢,测试用)
    step: bool = False          # 单步模式：每阶段(抽表/解析判定/...)暂停等确认


@app.post("/batch/start")
def batch_start(req: BatchStartRequest):
    """启动批量跑(后台线程)：解析→填台账；heal=True 时失败字段自动 LLM 自愈(完整生产流程)。"""
    import threading
    from src.batch_runner import run_batch, progress
    cur = progress()
    if cur.get("running"):
        return {"error": "已有批量在跑", "progress": cur}
    threading.Thread(target=run_batch,
                     args=(req.codes, req.year, req.db_write, req.heal, req.step),
                     daemon=True).start()
    return {"started": True, "total": len(req.codes), "heal": req.heal, "step": req.step}


@app.post("/batch/step/continue")
def batch_step_continue():
    """单步模式：放行当前断点，继续下一阶段。"""
    from src.batch_runner import _read, _write
    d = _read()
    d["step_continue"] = True
    _write(d)
    return {"ok": True}


@app.get("/batch/progress")
def batch_progress():
    """批量进度 + 分布(done/total/skipped/errors/fields_routed/by_reason/recent)。"""
    from src.batch_runner import progress
    return progress()


@app.post("/batch/control/{action}")
def batch_control(action: str):
    """跑批起停：pause|resume|stop。"""
    from src.batch_runner import control
    return control(action)


@app.get("/stocks/names")
def stocks_names(codes: str = None):
    """code → 公司名 映射（前端显示名称用）。codes 逗号分隔可筛；缺省返回全部。"""
    from src.database import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cl = [c.strip() for c in (codes or "").split(",") if c.strip()]
            if cl:
                cur.execute(f"SELECT code,name FROM stocks WHERE code IN ({','.join(['%s'] * len(cl))})", cl)
            else:
                cur.execute("SELECT code,name FROM stocks")
            return {"names": {r["code"]: r["name"] for r in cur.fetchall()}}
    finally:
        conn.close()


@app.get("/stocks/cached")
def stocks_cached():
    """只返回**有缓存 PDF**的 code→公司名（选公司只列能实际测的报告）。
    扫 PDF_CACHE_DIR 里的 {code}_{year}_*.pdf 取 code，去 stocks 表补名字。"""
    import glob
    import os
    from src.config import Config
    from src.database import get_conn
    codes = set()
    for p in glob.glob(str(Config.PDF_CACHE_DIR / "*.pdf")):
        code = os.path.basename(p).split("_", 1)[0]
        if code.isdigit():
            codes.add(code)
    if not codes:
        return {"names": {}, "count": 0}
    cl = sorted(codes)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT code,name FROM stocks WHERE code IN ({','.join(['%s'] * len(cl))})", cl)
            names = {r["code"]: r["name"] for r in cur.fetchall()}
    finally:
        conn.close()
    for c in cl:                       # 有缓存但 stocks 表缺名字的,也列出(名字回退=code)
        names.setdefault(c, c)
    return {"names": names, "count": len(cl)}


@app.get("/batch/candidates")
def batch_candidates(q: str = "", offset: int = 0, limit: int = 24, year: int = 2025):
    """可解析任务列表（有缓存 PDF 的报告）。支持 q(按 code/公司名搜) + 分页(offset/limit)。"""
    import glob
    import os
    from src.config import Config
    all_codes = sorted({os.path.basename(p).split("_")[0]
                        for p in glob.glob(str(Config.PDF_CACHE_DIR / f"*_{year}*.pdf"))})
    names = {}
    try:
        from src.database import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if all_codes:
                    cur.execute(f"SELECT code,name FROM stocks WHERE code IN "
                                f"({','.join(['%s'] * len(all_codes))})", all_codes)
                    names = {r["code"]: r["name"] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        names = {}
    ql = q.strip().lower()
    filtered = [c for c in all_codes
                if not ql or ql in c.lower() or ql in names.get(c, "").lower()]
    page = filtered[offset:offset + limit]
    return {"candidates": [{"code": c, "year": year, "name": names.get(c, "")} for c in page],
            "total": len(filtered), "offset": offset, "limit": limit}


# ── Agent 管理页 ──
# 注意路由顺序：静态段 /agents/routing 必须在参数段 /agents/{agent_id} 之前声明，否则会被后者吞掉。


@app.get("/agents")
def agents_list_api():
    from src.console_agents import agents_list
    return agents_list()


@app.get("/agents/routing")
def agents_routing_get_api():
    from src.console_agents import routing_get
    return routing_get()


class RoutingRequest(BaseModel):
    agent_id: str
    model: str = ""


@app.post("/agents/routing")
def agents_routing_set_api(req: RoutingRequest):
    from src.console_agents import routing_set
    return routing_set(req.agent_id, req.model)


@app.get("/agents/{agent_id}")
def agent_detail_api(agent_id: str):
    from src.console_agents import agent_detail
    return agent_detail(agent_id)


class AgentSaveRequest(BaseModel):
    system: str
    user: str
    version: str = ""


@app.post("/agents/{agent_id}")
def agent_save_api(agent_id: str, req: AgentSaveRequest):
    from src.console_agents import agent_save
    return agent_save(agent_id, req.system, req.user, req.version or None)


# ── 流水线成功率 / 链路 ──


@app.get("/pipeline/result")
def pipeline_result_api():
    """成功率结果 —— 从 DB(pipeline_runs) 每份最近一次汇总（DB 空回退 JSON）。"""
    from src.pipeline import result_from_db
    return result_from_db()


@app.get("/pipeline/failures")
def pipeline_failures_api(year: int = 2025, field: str = "revenue_breakdown"):
    """逐份失败分析(实时读 DB,不重解):每份非 committed 的结局/类别/占锚/LLM是否跑成 + 类别汇总。"""
    from src.pipeline import failure_analysis
    return failure_analysis(year, field)


# ── 超级工作台(管家) ──
@app.get("/steward/workbench/{code}")
def steward_workbench_api(code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """某公司的分层诊断档案(确定性,秒回)+ 本轮结局 —— 前端画成流程图。"""
    from src.console_steward import workbench
    return workbench(code, year, field)


@app.post("/steward/diagnose/{code}")
def steward_diagnose_api(code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """管家单案深诊断(强模型分层归因 → 根因/为什么没自愈/处方 + 裁决)。结果会存档。"""
    from src.console_steward import diagnose
    return diagnose(code, year, field)


@app.get("/steward/diagnosis/{code}")
def steward_saved_diagnosis_api(code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """读某公司已存档的管家诊断(无 LLM,秒回)——开页先显示历史诊断,没存过返回 {}。"""
    from src.console_steward import saved_diagnosis
    return saved_diagnosis(code, year, field)


@app.get("/steward/roadmap")
def steward_roadmap_api():
    """最近一次批量复盘的治理路线图(最大根因桶 + 自驱路线图)。"""
    from src.console_steward import roadmap
    return roadmap()


@app.get("/pipeline/progress")
def pipeline_progress_api():
    """实时批跑进度：phase / i / total / current(正在跑哪家) / done(已完成结局)。"""
    from src.pipeline import load_progress
    return load_progress() or {"phase": "idle"}


@app.get("/pipeline/chain")
def pipeline_chain_api(stock_code: str, year: int = 2025, field: str = "revenue_breakdown",
                      fresh: bool = False):
    """单字段整条链路 + 失败原因。默认读 DB 存好的(秒回)；fresh=1 实时重跑并写 DB。"""
    from src.pipeline import chain_from_db
    return chain_from_db(stock_code, year, field, recompute=fresh)


@app.get("/pipeline/llm")
def pipeline_llm_api(stock_code: str, year: int = 2025, field: str = "revenue_breakdown"):
    """按需跑 LLM：绿灯→复核 agent(选错表/跨页体检)，非绿灯→judge_diagnose 第一阶段。约 15~20s。结果写 DB。

    返回 run_field(..., use_llm=True) 的 dict，结构因 outcome 而异，公共字段如下：
      field: str          — 字段名，如 revenue_breakdown
      run_id: str         — 本次运行唯一 ID（process_events 可回放）
      outcome: str        — 终态，见下表
      via: str | None     — 路径：routed | cold | routed→冷启动回落 | domain | codegen 等
      reason: str | None  — 早退原因（no_input / out_of_scope 等）

    outcome 枚举（use_llm=True 时）：
      committed     — 复核 pass 或自愈后入库（可能有 committed: {...} 入库详情）
      verify_hold   — 复核 hold，交人工（handed_to_human=True, verdict/summary/suspects）
      no_such_table — 源文无目标表
      no_data       — 解析为空
      no_input      — 无 PDF 或未抽表
      no_anchor     — 无 DB 锚，确定性判不了（通常未进 LLM）
      out_of_scope  — 金融域外
      green         — 过锚但未跑完 LLM（极少，本接口恒 use_llm=True）
      non_green     — 非绿灯且 LLM 未改 outcome（use_llm=False 时常见）

    确定性解析（有 PDF/表时几乎总有）：
      value: dict | None       — 结构化解析结果 {维度: [{name, amount, ...}, ...]}
      confidence: str | None   — high / low / ...
      anchored: bool | None
      rule_version: str | None — base | 版本 id | base+跨页拼接
      version_sweep: list | None

    LLM / 自愈扩展（按路径出现，save_verify_run 会挑子集写 verify 列）：
      llm_kind: verify | diagnose | rule_heal | extract_heal | codegen | no_such_table
      verdict, summary, suspects, chat, reverify, reverify_detail, reverify_chat
      healed_select, heal, caliber_gap, routed_cat
      rule_heal, healed_rule, extract_heal, healed_extract, codegen, certified_parser
      decision, root_cause, next_action, evidence, diag_chat, heal_probe, steward
      llm_error: str           — LLM 异常时截断错误信息
    """
    from src.pipeline import run_field, save_verify_run
    rec = run_field(stock_code, year, field, use_llm=True)  # → Dict，见上方 docstring
    try:
        save_verify_run(stock_code, year, field, rec)
    except Exception:
        pass
    return rec


@app.get("/pipeline/timeline")
def pipeline_timeline_api(stock_code: str, year: int = 2025, field: str = "revenue_breakdown",
                          run_id: str = None, limit: int = 200):
    """案件时间线：优先读 process_events；无事件时回退 chain+verify 兼容视图。"""
    from src.pipeline import timeline_from_db
    return timeline_from_db(stock_code, year, field, run_id=run_id, limit=limit)


class PipelineRunRequest(BaseModel):
    codes: list
    year: int = 2025
    fields: list = None


@app.post("/pipeline/run")
def pipeline_run_api(req: PipelineRunRequest):
    """已废弃：请用 POST /pipeline/run_llm（完整 LLM 链）或 scripts 内 analyze_batch。"""
    _deprecated_410("POST /pipeline/run 已废弃，请用 POST /pipeline/run_llm")


class PipelineRunLLMRequest(BaseModel):
    codes: list = None            # None/空 → 跑 DB 里已有的全部；给 codes 只跑这些(增量,老数据不动)
    year: int = 2025
    field: str = "revenue_breakdown"


@app.post("/pipeline/run_llm")
def pipeline_run_llm_api(req: PipelineRunLLMRequest):
    """后台跑**完整 LLM 流水线**(复核+选表自愈+L2改规则+诊断)并写实时进度。
    立即返回，前端轮询 /pipeline/progress 看进度、/pipeline/result 看网格实时长。已在跑则拒绝重入。"""
    import threading
    from src.pipeline import run_full_pass, load_progress
    prog = load_progress() or {}
    if prog.get("phase") not in (None, "idle", "done"):
        return {"started": False, "error": "已有跑批在进行中", "phase": prog.get("phase"), "i": prog.get("i"), "total": prog.get("total")}
    codes = req.codes or None
    threading.Thread(target=lambda: run_full_pass(req.year, req.field, codes=codes, log=lambda *_: None),
                     daemon=True).start()
    return {"started": True, "n": len(codes) if codes else "all", "field": req.field}


# ── 启动入口 ──


@app.get("/download/list")
def download_list_api(board: str = "star", year: int = 2025):
    from src.console_service import download_list
    return download_list(board, year)


@app.post("/download/batch")
def download_batch_api(req: dict):
    from src.console_service import download_batch
    return download_batch(req.get("codes") or [], req.get("year", 2025))


if __name__ == "__main__":
    import uvicorn
    print(f"🟢 FinParseAI API 启动: http://localhost:{Config.PORT}")
    print(f"📖 API 文档: http://localhost:{Config.PORT}/docs")
    uvicorn.run(app, host="0.0.0.0", port=Config.PORT)
