"""
跑批看板 — Phase 4.5（CLI 版）

读取 run_state/<year>/ 实时汇总全自主跑批进度与质量分布，
并把死信队列按"红线字段 / 错误类型 / doc_type"聚类，方便定位下一步要修什么。

用法：
  python3 -m scripts.run_status                 # 默认 2025
  python3 -m scripts.run_status --year 2025
  python3 -m scripts.run_status --deadletter    # 展开死信明细
"""

import os
import sys
import json
import argparse
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

ALL_FIELDS = ["revenue_breakdown", "rnd_info", "employees",
              "cost_breakdown", "top_clients", "top_suppliers"]


def _load(jsonl):
    recs = []
    if os.path.exists(jsonl):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    return recs


def show(year=2025, show_dead=False):
    state_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "run_state", str(year))
    recs = _load(os.path.join(state_dir, "results.jsonl"))
    n = len(recs)
    if not n:
        print(f"暂无 {year} 跑批数据（{state_dir}/results.jsonl 不存在或为空）")
        return

    st = Counter(r.get("status", "?") for r in recs)
    clean = st.get("clean", 0)
    netp = sum(1 for r in recs if r.get("net_pass"))

    bar = lambda x: "█" * int(x / n * 30)
    print(f"\n{'='*58}\n  全自主跑批看板  {year}   共 {n} 份\n{'='*58}")
    print(f"  ✅ clean(硬规则过) {clean:>5} {clean/n*100:5.1f}%  {bar(clean)}")
    print(f"  ★ net_pass(6/6过) {netp:>5} {netp/n*100:5.1f}%  {bar(netp)}")
    print(f"  ❌ red(红线)       {st.get('red',0):>5}")
    print(f"  💥 error           {st.get('error',0):>5}")
    print(f"  ⏱  timeout         {st.get('timeout',0):>5}")

    print(f"\n  ── 字段覆盖 ──")
    for fld in ALL_FIELDS:
        c = sum(1 for r in recs if r.get("present", {}).get(fld))
        print(f"     {fld:20s} {c:>4}/{n} {c/n*100:5.0f}%  {bar(c)}")

    print(f"\n  ── doc_type 分布 ──  {dict(Counter(r.get('doc_type','?') for r in recs))}")

    # 死信聚类
    dead = [r for r in recs if r.get("status") in ("red", "error", "timeout")]
    if dead:
        print(f"\n  ── 死信队列 {len(dead)} 份 聚类 ──")
        red_rule = Counter()
        for r in dead:
            for v in r.get("violations", []):
                if v.get("severity") == "red":
                    red_rule[f"{v['field'].split('.')[0]}:{v['rule']}"] += 1
        print(f"     红线规则 TOP: {red_rule.most_common(6)}")
        err = Counter(r.get("error", "")[:40] for r in dead if r.get("error"))
        if err:
            print(f"     错误类型 TOP: {err.most_common(4)}")
        if show_dead:
            print(f"\n  ── 死信明细 ──")
            for r in dead[:50]:
                print(f"     {r['stock_code']} [{r.get('status')}] "
                      f"{r.get('red_fields') or r.get('error','')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--deadletter", action="store_true")
    args = ap.parse_args()
    show(year=args.year, show_dead=args.deadletter)


if __name__ == "__main__":
    main()
