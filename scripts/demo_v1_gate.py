"""
灵魂时刻演示 — LLM(本轮=Claude)写的 v1 能否在闸前打败 v0

对 000425（选错表陷阱）：
  v0 = 现有通用解析器（re-parse）
  v1 = src/parsers/versions/rev_000425_v1.py（跑抽表缓存）
  闸 = accept_candidate：v1 整体提升且零退步才收

作用域：bespoke v1 针对 000425 版式，故闸只在 000425 golden 上比（registry 按版式路由）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.eval.run_eval import load_golden, eval_version, accept_candidate
from src.eval.sandbox_exec import version_parse_fn
from scripts.eval_current_parser import parse_fn as v0_fn

V1_PATH = "src/parsers/versions/rev_000425_v1.py"


def main():
    gold = load_golden()
    g425 = [e for e in gold if e["stock_code"] == "000425"]
    if not g425:
        print("❌ golden 里没有 000425"); return

    v1_fn = version_parse_fn(V1_PATH)

    print("跑 v0(现有解析器, re-parse)…")
    r0 = eval_version(v0_fn, g425)
    print("跑 v1(专用版, 缓存表)…")
    r1 = eval_version(v1_fn, g425)
    gate = accept_candidate(v0_fn, v1_fn, g425)

    print(f"\n{'='*56}\n⚔️  000425 对决（golden: industries+segments+regions 共10行）")
    print(f"   v0 均分 {r0['summary']['mean_score']}  exact {r0['summary']['exact']}/{r0['summary']['n']}")
    print(f"   v1 均分 {r1['summary']['mean_score']}  exact {r1['summary']['exact']}/{r1['summary']['n']}")
    print(f"{'='*56}")
    print(f"🚪 闸判决: {'✅ 收下 v1' if gate['accepted'] else '❌ 拒'}  "
          f"(base={gate['base_score']} → cand={gate['candidate_score']}, "
          f"提升={gate['improved']}, 退步={gate['regressions']})")

    for tag, r in [("v0", r0), ("v1", r1)]:
        rep = r["per_report"][0]
        print(f"\n  [{tag}] 000425 分={rep['score']} exact={rep.get('exact')}")
        for m in (rep.get("mismatches") or [])[:8]:
            print(f"      ✗ [{m.get('dim')}] {m.get('name')}: {m.get('issue')}")


if __name__ == "__main__":
    main()
