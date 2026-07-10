"""
跑 LangGraph 自愈级联 demo。

用法:
  # 正常跑一份(自然流经 冷启动→复核→(hold则)复用/选表自愈/管家):
  PYTHONPATH=. python3 demos/langgraph_min/run.py 000785

  # 演示 HITL:复核一 hold 直奔人审 → 图在 human 前**暂停**(状态落 SQLite checkpointer)
  #          → 脚本模拟人工 approve → 从断点**恢复** → 入库
  PYTHONPATH=. python3 demos/langgraph_min/run.py 300014 --force-human --approve

关键看点(对面试官讲):
  1. 节点调的是**生产真实 agent**(verify=DeepSeek / steward=通义),不是 mock。
  2. `interrupt_before=['human']` + SqliteSaver = **真人审闭环 + 状态机持久化**(进程杀了也能从断点恢复)。
  3. 同一条自愈级联,生产是手写状态机 + 双闸;这里是 StateGraph——面试讲"框架何时该用"。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from langgraph.checkpoint.sqlite import SqliteSaver     # noqa: E402
from demos.langgraph_min.graph import build_graph        # noqa: E402


def _print_events(evs):
    for i, e in enumerate(evs or [], 1):
        extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("node", "msg") and v not in (None, ""))
        print(f"  {i:>2}. [{e['node']:<12}] {e['msg']}" + (f"   ({extra})" if extra else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--field", default="revenue_breakdown")
    ap.add_argument("--force-human", action="store_true", help="复核 hold 直奔人审(演示 interrupt)")
    ap.add_argument("--approve", action="store_true", help="人审注入 approve(否则 reject)")
    args = ap.parse_args()

    db = os.path.join(os.path.dirname(__file__), "checkpoints.sqlite")
    thread = f"{args.code}_{args.year}_{args.field}"
    config = {"configurable": {"thread_id": thread}}
    init = {"code": args.code, "year": args.year, "field": args.field, "events": []}

    print(f"▶ LangGraph 自愈级联 demo · {thread}"
          + ("  [force-human]" if args.force_human else ""))
    print("  节点调真实 agent:verify=DeepSeek复核 · steward=通义qwen裁决 · heal0=按骨架复用认证解析器\n")

    with SqliteSaver.from_conn_string(db) as saver:      # 状态机持久化(JD加分)
        graph = build_graph(checkpointer=saver, force_human=args.force_human)

        # 第一段:跑到终点 或 在 human 前中断
        state = graph.invoke(init, config)
        snap = graph.get_state(config)

        if snap.next and "human" in snap.next:           # 命中 interrupt_before=['human']
            print("⏸  图在【人审】前暂停 —— 状态已落 SQLite checkpointer(进程杀掉也能恢复)")
            _print_events(state.get("events"))
            decision = "approve" if args.approve else "reject"
            print(f"\n👤 人工处置:注入 human_decision={decision} → 从断点恢复\n")
            graph.update_state(config, {"human_decision": decision})
            state = graph.invoke(None, config)           # resume:传 None = 从 checkpoint 续跑

        print("─" * 60)
        print("事件时间线:")
        _print_events(state.get("events"))
        print("─" * 60)
        print(f"✅ 终态 outcome={state.get('outcome')} · via={state.get('via') or 'cold'}")


if __name__ == "__main__":
    main()
