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
    return {"status": "ok", "service": "FinParseAI", "version": "0.1.0"}


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
    """
    执行带迭代闭环的完整解析流程（解析→校验→优化→重试→人工兜底）。
    
    参数同 /parse/by-code，但包含最多3次自动迭代。
    """
    # 查找 PDF 缓存
    cache_dir = Config.PDF_CACHE_DIR
    pdf_path = None
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if f.suffix != ".pdf":
                continue
            parts = f.stem.split("_")
            if len(parts) >= 2 and parts[0] == stock_code and parts[1] == str(year):
                pdf_path = str(f)
                break

    if not pdf_path:
        raise HTTPException(status_code=404, detail=f"PDF 未找到: {stock_code}_{year}.pdf")

    stock = find_stock(stock_code)
    company_name = stock["name"] if stock else None

    from src.agents.iteration import IterationEngine
    try:
        engine = IterationEngine()
        report = engine.run(
            pdf_path=pdf_path,
            stock_code=stock_code,
            report_year=year,
            company_name=company_name,
            db_write=True,
        )
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 人工复核端点 ──

@app.get("/review/tasks")
def list_review_tasks(limit: int = 20, pending_only: bool = False):
    """列出复核任务"""
    from src.review.manager import ReviewManager
    mgr = ReviewManager()
    if pending_only:
        tasks = mgr.list_pending(limit=limit)
    else:
        tasks = mgr.list_all(limit=limit)
    return {"tasks": tasks, "count": len(tasks)}


@app.get("/review/task/{task_id}")
def get_review_task(task_id: int):
    """获取复核任务详情"""
    from src.review.manager import ReviewManager
    mgr = ReviewManager()
    task = mgr.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.post("/review/{task_id}/start")
def start_review(task_id: int, reviewer: str = "admin"):
    """开始审核"""
    from src.review.manager import ReviewManager
    mgr = ReviewManager()
    ok = mgr.start_review(task_id, reviewer=reviewer)
    return {"success": ok, "task_id": task_id}


@app.post("/review/{task_id}/approve")
def approve_review(task_id: int, comment: str = ""):
    """审核通过"""
    from src.review.manager import ReviewManager
    mgr = ReviewManager()
    ok = mgr.approve(task_id, comment=comment)
    return {"success": ok, "task_id": task_id}


@app.post("/review/{task_id}/reject")
def reject_review(task_id: int, comment: str = ""):
    """驳回"""
    from src.review.manager import ReviewManager
    mgr = ReviewManager()
    ok = mgr.reject(task_id, comment=comment)
    return {"success": ok, "task_id": task_id}


@app.post("/review/{task_id}/fix")
def apply_fix(task_id: int, fixes: dict, comment: str = ""):
    """应用人工修正数据"""
    from src.review.manager import ReviewManager
    mgr = ReviewManager()
    result = mgr.apply_manual_fix(task_id, fixes, comment=comment)
    return result


# ── 导出端点 ──

@app.get("/export/json")
def export_parse_json(stock_code: str = None, year: int = None, limit: int = 10):
    """导出解析结果为 JSON"""
    from src.export.exporter import export_json
    return {"records": export_json(stock_code=stock_code, year=year, limit=limit)}


@app.get("/export/csv")
def export_parse_csv(stock_code: str = None, year: int = None, limit: int = 100):
    """导出解析结果为 CSV"""
    from src.export.exporter import export_csv
    from fastapi.responses import PlainTextResponse
    csv_content = export_csv(stock_code=stock_code, year=year, limit=limit)
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=finparse_export.csv"},
    )


@app.get("/export/versions")
def export_parser_versions(limit: int = 20):
    """获取解析器版本历史"""
    from src.export.exporter import get_parser_version_history
    return {"versions": get_parser_version_history(limit=limit)}


# ── 控制 / 审核台（给前端 console）──

class RecodeRequest(BaseModel):
    stock_code: str
    year: int = 2025
    code: str


@app.post("/control/{action}")
def console_control(action: str):
    """暂停/继续/停止跑批。action ∈ pause|resume|stop。"""
    from src.console_service import control
    return control(action)


@app.get("/heal/records")
def console_heal_records():
    """自愈活动记录（路由实跑产出）。"""
    from src.console_service import heal_records
    return {"records": heal_records()}


@app.post("/review/recode")
def console_recode(req: RecodeRequest):
    """人改解析器代码 → 重过闸 → 返回 {score, exact, mismatches}。"""
    from src.console_service import recode
    return recode(req.stock_code, req.year, req.code)


@app.get("/review/task")
def console_review_task(stock_code: str, year: int = 2025):
    """审核任务：结果 + 溯源(page/bbox) + 渲染页(base64) + 解析器源码。"""
    from src.console_service import review_task
    return review_task(stock_code, year)


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
def triage_queue_list(reason: str = None, field: str = None):
    """分诊待办列表（默认只列 open）。reason/field 可选筛选。"""
    from src.eval.triage_queue import list_open
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
    from src.parsers.revenue_router import route_field
    from src.agents.llm_judge import judge_field
    if req.field not in FIELDS:
        return {"verdict": "unknown", "summary": f"未知字段 {req.field}", "field": req.field}
    _ensure_cached(req.stock_code, req.year)
    spec = get_spec(req.field)
    rt = route_field(spec, req.stock_code, req.year)
    value = rt.get("result")
    if isinstance(value, dict) and req.field in value:        # D类富结构解包
        value = value[req.field]
    if value is None:
        return {"verdict": "unknown", "summary": "该字段未路由出结果，无法裁判", "field": req.field}
    return judge_field(req.field, req.stock_code, req.year, value, spec=spec)


@app.get("/review/signals")
def review_signals(stock_code: str, year: int = 2025):
    """每字段的置信度信号(#1跨表锚) + DB锚值，供审核台展示徽章。"""
    from src.eval.field_spec import FIELDS, get_spec
    from src.parsers.revenue_router import route_field
    from src.eval.anchors import get_anchors
    _ensure_cached(stock_code, year)
    out = {}
    for field in FIELDS:
        rt = route_field(get_spec(field), stock_code, year)
        sig = rt.get("signal") or {}
        out[field] = {"status": rt.get("status"), "clean": sig.get("clean"),
                      "confidence": sig.get("confidence"), "anchored": sig.get("anchored"),
                      "anchor": sig.get("anchor")}
    return {"signals": out, "anchors": get_anchors(stock_code, year)}


# ── 启动入口 ──

if __name__ == "__main__":
    import uvicorn
    print(f"🟢 FinParseAI API 启动: http://localhost:{Config.PORT}")
    print(f"📖 API 文档: http://localhost:{Config.PORT}/docs")
    uvicorn.run(app, host="0.0.0.0", port=Config.PORT)
