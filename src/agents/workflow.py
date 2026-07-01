"""
LangGraph Agent 调度流水线 — Phase 3 条件路由闭环

状态机（含分支与迭代回边）：

  parse_pdf → validate → decide ─┬─ archive(归档/写库) → report → END
                                 ├─ optimize → parse_pdf   (迭代回边，最多 N 次)
                                 └─ human_review           → report → END

校验闸门（正确率优先）：
  - 关键字段勾稽硬规则(hard_rules)为**红线**：有 red 违规一律视为未通过，
    无论向量/LLM 怎么判。
  - 向量/LLM 校验作为补充（默认可跳过，第二阶段启用）。

决策（decide）：
  - 硬规则通过 且 字段足够        → archive
  - 未通过 且 迭代未达上限 且 可修 → optimize（改规则后回到 parse_pdf 重解析）
  - 未通过 且 已达上限/不可修      → human_review（死信 / 人工兜底）

用法:
  from src.agents.workflow import run_parse_workflow
  result = run_parse_workflow("002407", 2025, "xxx.pdf", db_write=False)
"""

import time
from typing import Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END

from src.config import Config
from src.engine_orchestrator import FinParseAI
from src.validators.hard_rules import check_hard_rules


# ── 图状态 ──

class ParseState(TypedDict, total=False):
    # 输入
    stock_code: str
    report_year: int
    pdf_path: str
    company_name: Optional[str]
    db_write: bool
    skip_vector: bool          # 跳过向量/LLM 校验（硬规则始终执行）
    max_iterations: int

    # 解析
    parse_result: Optional[Dict]
    parsed_fields: List[str]
    missing_fields: List[str]
    field_count: int

    # 校验
    hard_report: Optional[Dict]
    vector_report: Optional[Dict]
    validation_passed: bool

    # 决策 / 迭代
    iteration: int
    route: str                 # archive | optimize | human_review
    optimization_log: List[Dict]

    # 输出
    db_write_status: Optional[str]
    final_status: str
    start_time: float
    duration: float


_engine: Optional[FinParseAI] = None


def _get_engine() -> FinParseAI:
    global _engine
    if _engine is None:
        _engine = FinParseAI()
    return _engine


# ── 节点 ──

ALL_FIELDS = ["revenue_breakdown", "rnd_info", "employees",
              "cost_breakdown", "top_clients", "top_suppliers"]


def node_parse_pdf(state: ParseState) -> Dict:
    it = state.get("iteration", 0)
    print(f"  [Agent] 📄 解析(iter {it}): {state['stock_code']} {state['report_year']}")
    r = _get_engine().run(
        state["pdf_path"], stock_code=state["stock_code"],
        report_year=state["report_year"], company_name=state.get("company_name"),
        db_write=False,
    )
    parsed = [f for f in ALL_FIELDS if r.get(f)]
    return {
        "parse_result": r,
        "parsed_fields": parsed,
        "missing_fields": [f for f in ALL_FIELDS if not r.get(f)],
        "field_count": r.get("field_count", 0),
    }


def node_validate(state: ParseState) -> Dict:
    r = state.get("parse_result") or {}

    # ── 红线：关键字段勾稽硬规则（永远执行，不可跳过） ──
    hard = check_hard_rules(r)
    print(f"  [Agent] 🔒 硬规则: {'✅通过' if hard['passed'] else '❌红线'} "
          f"(red={hard['red_count']} warn={hard['warn_count']})")

    # ── 补充：向量/LLM 校验（可跳过） ──
    vector_report = None
    if not state.get("skip_vector"):
        try:
            from src.validators.vector_validator import VectorValidator
            vector_report = VectorValidator().validate(r)
            print(f"  [Agent] 🔍 向量校验: {'✅' if vector_report['passed'] else '❌'}")
        except Exception as e:
            print(f"  [Agent] ⚠️ 向量校验跳过: {e}")

    # 综合：硬规则红线优先；向量校验作为附加约束
    passed = hard["passed"] and (vector_report["passed"] if vector_report else True)
    return {"hard_report": hard, "vector_report": vector_report, "validation_passed": passed}


def node_decide(state: ParseState) -> Dict:
    """决策中枢：设置 route。"""
    passed = state.get("validation_passed", False)
    fc = state.get("field_count", 0)
    it = state.get("iteration", 0)
    max_it = state.get("max_iterations", Config.MAX_ITERATE)
    hard = state.get("hard_report") or {}

    if passed:
        route = "archive"
    elif it < max_it and (hard.get("red_count", 0) > 0 or fc < 6):
        route = "optimize"     # 还有迭代额度且存在可修复点
    else:
        route = "human_review"

    print(f"  [Agent] 🧭 决策: route={route} (passed={passed} fc={fc} iter={it}/{max_it})")
    return {"route": route}


def node_optimize(state: ParseState) -> Dict:
    """诊断 + 优化规则，然后迭代回 parse_pdf 重解析。"""
    it = state.get("iteration", 0) + 1
    log = list(state.get("optimization_log", []))
    try:
        from src.agents.optimizer import ParseOptimizer
        opt = ParseOptimizer()
        decision = opt.diagnose(state.get("parse_result") or {},
                                state.get("vector_report") or _hard_to_vectorlike(state))
        applied = {}
        if decision.get("suggested_action") not in (None, "none"):
            applied = opt.apply(decision, stock_code=state.get("stock_code"))
        log.append({"iteration": it, "root_cause": decision.get("root_cause"),
                    "action": decision.get("suggested_action"),
                    "applied": applied.get("status")})
        print(f"  [Agent] 🔧 优化(iter {it}): {decision.get('root_cause')} → {applied.get('status','-')}")
    except Exception as e:
        log.append({"iteration": it, "error": str(e)})
        print(f"  [Agent] ⚠️ 优化失败: {e}")
    return {"iteration": it, "optimization_log": log}


def _hard_to_vectorlike(state: ParseState) -> Dict:
    """无向量报告时，把硬规则结果包装成 optimizer.diagnose 可消费的形态。"""
    hard = state.get("hard_report") or {}
    reports = [{"abnormal_type": "逻辑错误", "abnormal_position": v["field"],
                "error_detail": v["detail"], "similarity_score": 0,
                "suggest_action": "modify_parser"}
               for v in hard.get("violations", []) if v.get("severity") == "red"]
    return {"passed": hard.get("passed", True), "abnormal_reports": reports,
            "semantic_checks": [], "checks": {"passed": 0, "total": len(reports)}}


def node_db_write(state: ParseState) -> Dict:
    """仅归档路径写库（且需 db_write=True）。"""
    if not state.get("db_write"):
        return {"db_write_status": "skipped"}
    r = state.get("parse_result") or {}
    fields = {f: r[f] for f in ALL_FIELDS if r.get(f)}
    if not fields:
        return {"db_write_status": "no_fields"}
    try:
        from src.database import get_conn, update_report_fields, reports_table
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM `{reports_table()}` WHERE stock_code=%s AND report_year=%s "
                "AND report_quarter='annual' LIMIT 1",
                (state["stock_code"], state["report_year"]),
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return {"db_write_status": "report_not_found"}
        fields["data_source"] = "hybrid"
        fields["pdf_parsed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        update_report_fields(row["id"], fields)
        return {"db_write_status": "success"}
    except Exception as e:
        return {"db_write_status": f"error: {e}"}


def node_human_review(state: ParseState) -> Dict:
    print(f"  [Agent] 🙋 转人工复核: {state['stock_code']} "
          f"(红线字段: {(state.get('hard_report') or {}).get('red_fields')})")
    return {"final_status": "needs_review"}


def node_report(state: ParseState) -> Dict:
    route = state.get("route")
    final = state.get("final_status") or ("success" if route == "archive" else route)
    dur = time.time() - state.get("start_time", time.time())
    print(f"  [Agent] 📊 完成: status={final} fields={state.get('field_count')}/6 "
          f"iters={state.get('iteration',0)} {dur:.1f}s db={state.get('db_write_status')}")
    return {"final_status": final, "duration": dur}


# ── 构图 ──

def _route_from_decide(state: ParseState) -> str:
    return state.get("route", "human_review")


def create_parse_workflow():
    g = StateGraph(ParseState)
    g.add_node("parse_pdf", node_parse_pdf)
    g.add_node("validate", node_validate)
    g.add_node("decide", node_decide)
    g.add_node("optimize", node_optimize)
    g.add_node("db_write", node_db_write)
    g.add_node("human_review", node_human_review)
    g.add_node("report", node_report)

    g.set_entry_point("parse_pdf")
    g.add_edge("parse_pdf", "validate")
    g.add_edge("validate", "decide")
    g.add_conditional_edges("decide", _route_from_decide, {
        "archive": "db_write",
        "optimize": "optimize",
        "human_review": "human_review",
    })
    g.add_edge("optimize", "parse_pdf")     # 迭代回边
    g.add_edge("db_write", "report")
    g.add_edge("human_review", "report")
    g.add_edge("report", END)
    return g.compile()


def run_parse_workflow(stock_code: str, report_year: int, pdf_path: str,
                       company_name: Optional[str] = None, db_write: bool = False,
                       skip_vector: bool = True, max_iterations: int = None) -> Dict:
    app = create_parse_workflow()
    state = {
        "stock_code": stock_code, "report_year": report_year, "pdf_path": pdf_path,
        "company_name": company_name, "db_write": db_write, "skip_vector": skip_vector,
        "max_iterations": max_iterations or Config.MAX_ITERATE,
        "iteration": 0, "optimization_log": [], "start_time": time.time(),
    }
    # 防止迭代回边导致超出递归上限
    return app.invoke(state, config={"recursion_limit": 50})
