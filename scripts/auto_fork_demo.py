"""
fork 路径演示 — 给 DeepSeek 一个"相似版式的部分正确母本"，让它 fork 改到 exact

对比：from-scratch DeepSeek 在 000425 撞天花板(卡0.44转人工)；
这里给它一个已做对 industries(含100%行难点)+segments、只缺 regions 的母本去 fork，
看它能不能补上 regions 到 exact——验证"fork 改比从零写容易、能救弱模型"。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.eval.run_eval import load_golden
from src.eval.sandbox_exec import version_parse_fn
from src.agents.code_generator import repair

CODE, YEAR = "000425", 2025
MOTHER = "src/parsers/versions/rev_demo_partial_mother.py"
OUT = "src/parsers/versions/rev_000425_forked.py"


def main():
    g = [e for e in load_golden() if e["stock_code"] == CODE]
    if not g:
        print("❌ 无 golden"); return
    catalog = [{"key": "相似版式母本(已对industries+segments,缺regions)", "path": MOTHER}]
    base_fn = version_parse_fn(MOTHER)   # 不退步基准 = 母本（快，跑缓存表）

    print(f"🤖 fork 演示：让 {Config.LLM_MODEL} 在母本基础上改到完全正确…")
    r = repair(CODE, YEAR, g[0], base_fn, OUT, catalog=catalog)

    print(f"\n{'='*56}")
    print(f"决策路径: {r.get('action')}")
    if r.get("accepted") and r.get("action") == "fork":
        print(f"🎉 fork 成功！第 {r['rounds']} 轮改到 exact(1.0) — 弱模型靠母本救回")
    elif r.get("accepted"):
        print(f"结果: {r}")
    else:
        print(f"🙋 fork 后仍未 exact(最好 {r.get('best_score')}) → 转人工")


if __name__ == "__main__":
    main()
