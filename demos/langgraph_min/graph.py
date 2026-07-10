"""
LangGraph 复刻 FinParseAI 自愈级联(简化版)—— 求职 demo。

要点:**节点里调的是 FinParseAI 生产用的真实 agent**(不是 mock):
  · verify   → src.agents.llm_judge.verify_field        (DeepSeek 复核)
  · heal0    → src.pipeline._routed_reuse               (按选中表骨架复用认证解析器)
  · steward  → src.agents.steward_agent.steward_adjudicate (通义 qwen 二次裁决)
  · parse    → src.pipeline._parse_versioned            (冷启动:default.py+版本池+跨页拼)

生产链是手写状态级联 + 双闸 enforce(见 src/pipeline.py:run_field);这里用 LangGraph 把
**同一条自愈级联**表达成 StateGraph:节点 + 条件边 + 环 + 状态持久化(checkpointer)+ 人审中断(interrupt)。
面试叙事见 README.md。
"""

import operator
from typing import Annotated, List, Optional, TypedDict

from langgraph.graph import StateGraph, START, END


# ── 图状态:一份报告在链路里流动的全部信息(对应生产里到处 threading 的 rec dict)──
class HealState(TypedDict):
    code: str
    year: int
    field: str
    value: Optional[dict]                       # 解析出的结构化 JSON
    sig: Optional[dict]                          # 金额锚信号(confidence high/low)
    conf: Optional[str]
    verdict: Optional[str]                       # 复核结论 pass/hold
    suspects: list
    outcome: Optional[str]                       # committed / verify_hold / needs_human / no_data
    via: Optional[str]                           # 走哪条路入库(cold / heal0复用 / 选表自愈 / 人工)
    human_decision: Optional[str]                # 人审注入(approve/reject)
    events: Annotated[List[dict], operator.add]  # 观测事件流(reducer 累加,对应生产 emit_event)


def _ev(node: str, msg: str, **kw) -> dict:
    return {"node": node, "msg": msg, **kw}


# ── 一次性取该报告的确定性上下文(spec/tables/pdf/anchors),多个节点共用 ──
def _ctx(state: HealState):
    from src.eval.field_spec import get_spec
    from src.eval.table_cache import get_tables
    from src.pipeline import _pdf
    from src.eval.anchors import get_anchors
    code, year, field = state["code"], state["year"], state["field"]
    return (get_spec(field), get_tables(code, year), _pdf(code, year),
            get_anchors(code, year) or {})


# ── 节点:每个都是 state -> 局部更新;调真实 agent ──

def parse_cold(state: HealState) -> dict:
    """冷启动解析(主路径)+ 金额锚判。"""
    from src.pipeline import _parse_versioned
    spec, tables, pdf, anchors = _ctx(state)
    if not tables or not pdf:
        return {"outcome": "no_data", "events": [_ev("parse_cold", "无表/无PDF")]}
    pv = _parse_versioned(state["code"], state["year"], state["field"], tables, pdf, anchors, spec)
    value, sig = pv.get("value"), pv.get("sig") or {}
    conf = sig.get("confidence")
    n = sum(len(v) for v in (value or {}).values() if isinstance(v, list))
    return {"value": value, "sig": sig, "conf": conf,
            "events": [_ev("parse_cold", f"冷启动解析 {n} 行 · 锚判={conf}", rows=n, conf=conf)]}


def verify(state: HealState) -> dict:
    """真实复核 agent(DeepSeek):源文锚。"""
    from src.agents.llm_judge import verify_field
    spec, _, _, _ = _ctx(state)
    v = verify_field(state["field"], state["code"], state["year"], state["value"],
                     sig=state.get("sig"), spec=spec)
    verdict = v.get("verdict")
    return {"verdict": verdict, "suspects": v.get("suspects") or [],
            "events": [_ev("verify", f"复核(DeepSeek)= {verdict}", verdict=verdict,
                           summary=(v.get("summary") or "")[:80])]}


def heal0_reuse(state: HealState) -> dict:
    """heal-step-0:按选中表骨架复用已认证解析器(最便宜的 healer)。"""
    from src.pipeline import _routed_reuse
    spec, _, _, anchors = _ctx(state)
    try:
        r = _routed_reuse(state["code"], state["year"], state["field"], spec, anchors)
    except Exception as e:
        return {"events": [_ev("heal0_reuse", f"复用异常: {str(e)[:60]}")]}
    if r and r.get("outcome") == "committed":
        return {"value": r.get("value"), "outcome": "committed", "via": "heal0复用认证解析器",
                "events": [_ev("heal0_reuse", f"命中 {(r.get('reused_parser') or '').split('/')[-1]} · 过双闸",
                               reused=r.get("reused_parser"))]}
    return {"events": [_ev("heal0_reuse", "没命中认证解析器 → 交下游 healer")]}


def heal_select(state: HealState) -> dict:
    """选表自愈(真实):复核喊选错表时重选目标表再解析再复核。"""
    from src.pipeline import _heal_and_verify
    spec, _, _, _ = _ctx(state)
    try:
        rec, healed = _heal_and_verify(state["code"], state["year"], state["field"], spec)
    except Exception as e:
        return {"events": [_ev("heal_select", f"选表自愈异常: {str(e)[:60]}")]}
    if healed:
        return {"value": rec.get("value"), "outcome": "committed", "via": "选表自愈",
                "events": [_ev("heal_select", "重选目标表 → 重解析 → 复核 pass")]}
    return {"events": [_ev("heal_select", "选表自愈没选到更好的表 → 交管家")]}


def steward(state: HealState) -> dict:
    """管家 A·二次裁决(真实,通义 qwen 强模型):过锚但弱模型 hold → 强模型重判。"""
    from src.agents.steward_agent import steward_adjudicate
    spec, _, _, _ = _ctx(state)
    try:
        adj = steward_adjudicate(state["code"], state["year"], state["field"],
                                 state["value"], state.get("sig") or {"confidence": "high"}, spec)
    except Exception as e:
        return {"events": [_ev("steward", f"管家异常: {str(e)[:60]}")]}
    dec = adj.get("decision")
    if dec == "commit":
        return {"outcome": "committed", "via": "管家裁决(强模型判假hold)",
                "events": [_ev("steward", "通义 qwen 判假 hold → 入库", decision=dec)]}
    return {"events": [_ev("steward", f"通义 qwen 判 {dec}: {(adj.get('cause') or '')[:60]}", decision=dec)]}


def human_review(state: HealState) -> dict:
    """人审节点。图 compile 时 interrupt_before=['human']:执行到这**之前**暂停(状态落 checkpointer),
    人在前端处置后 update_state 注入 human_decision 再 resume,才跑到这里。"""
    dec = state.get("human_decision") or "reject"
    if dec == "approve":
        return {"outcome": "committed", "via": "人工批准",
                "events": [_ev("human", "人工批准 → 入库")]}
    return {"outcome": "needs_human", "via": "人工驳回",
            "events": [_ev("human", "人工驳回 → 留分诊队列")]}


def commit(state: HealState) -> dict:
    """入库(demo 默认 dry-run,不写 DB;生产是 _auto_commit)。"""
    return {"outcome": "committed",
            "events": [_ev("commit", f"入库 ✓ via={state.get('via') or 'cold'}(demo dry-run)")]}


def done(state: HealState) -> dict:
    return {"outcome": state.get("outcome") or "no_data",
            "events": [_ev("done", f"终态={state.get('outcome')}")]}


# ── 条件边:级联的路由表(取代生产里 _green_llm/_nongreen_llm 的嵌套 if/else)──

def route_after_parse(state: HealState) -> str:
    if not state.get("value"):
        return "done"                      # 冷启动无结果
    if state.get("conf") == "high":
        return "verify"                    # 过锚 → 复核
    return "heal0_reuse"                    # 不过锚 → 自愈级联(先试复用)


def route_after_verify(state: HealState) -> str:
    return "commit" if state.get("verdict") == "pass" else "heal0_reuse"  # 复核 hold → 先试复用


def route_after_heal0(state: HealState) -> str:
    return "commit" if state.get("outcome") == "committed" else "heal_select"


def route_after_select(state: HealState) -> str:
    return "commit" if state.get("outcome") == "committed" else "steward"


def route_after_steward(state: HealState) -> str:
    return "commit" if state.get("outcome") == "committed" else "human"


def route_after_human(state: HealState) -> str:
    return "commit" if state.get("outcome") == "committed" else "done"


def build_graph(checkpointer=None, force_human: bool = False):
    """装配图。force_human=True:复核一 hold 直接送人审(跳过 healer)——保证 demo 能演到 interrupt。"""
    g = StateGraph(HealState)
    for name, fn in [("parse_cold", parse_cold), ("verify", verify),
                     ("heal0_reuse", heal0_reuse), ("heal_select", heal_select),
                     ("steward", steward), ("human", human_review),
                     ("commit", commit), ("done", done)]:
        g.add_node(name, fn)

    g.add_edge(START, "parse_cold")
    g.add_conditional_edges("parse_cold", route_after_parse,
                            {"verify": "verify", "heal0_reuse": "heal0_reuse", "done": "done"})
    # force_human:复核 hold 直奔人审(演示 interrupt);否则走完整 healer 级联
    g.add_conditional_edges("verify",
                            (lambda s: "commit" if s.get("verdict") == "pass" else "human")
                            if force_human else route_after_verify,
                            {"commit": "commit", "heal0_reuse": "heal0_reuse", "human": "human"})
    g.add_conditional_edges("heal0_reuse", route_after_heal0,
                            {"commit": "commit", "heal_select": "heal_select"})
    g.add_conditional_edges("heal_select", route_after_select,
                            {"commit": "commit", "steward": "steward"})
    g.add_conditional_edges("steward", route_after_steward,
                            {"commit": "commit", "human": "human"})
    g.add_conditional_edges("human", route_after_human,
                            {"commit": "commit", "done": "done"})
    g.add_edge("commit", END)
    g.add_edge("done", END)

    # interrupt_before=['human']:执行到人审**之前**暂停 + 落 checkpointer → 真 HITL(暂停-人处置-恢复)
    return g.compile(checkpointer=checkpointer, interrupt_before=["human"])
