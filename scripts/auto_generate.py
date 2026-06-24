"""
自动生成演示 — LLM(DeepSeek) 自己写出能过闸的专用解析器

对 000425：v0 现有解析器失败(0分)，让生成 agent 自动写 v_auto → 沙箱→闸。
全自动，无人手写解析代码。

用法：
  python3 -m scripts.auto_generate
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.engine_orchestrator import FinParseAI
from src.eval.run_eval import load_golden
from src.agents.code_generator import generate_parser

CODE, YEAR = "000425", 2025
OUT = "src/parsers/versions/rev_000425_auto.py"


def main():
    gold = [e for e in load_golden() if e["stock_code"] == CODE]
    if not gold:
        print(f"❌ golden 里没有 {CODE}"); return
    golden_entry = gold[0]

    # v0 基线：现有解析器跑一次，缓存成常量 base_fn（避免每轮重跑慢）
    print(f"跑 v0 基线({CODE})…")
    eng = FinParseAI()
    hits = sorted(Config.PDF_CACHE_DIR.glob(f"{CODE}_{YEAR}*.pdf"))
    v0_rb = eng.run(str(hits[0]), stock_code=CODE, report_year=YEAR, db_write=False).get("revenue_breakdown")

    def base_fn(c, y):
        return v0_rb

    print(f"\n🤖 让 LLM({Config.LLM_MODEL}) 自动写解析器（终点=完全正确）…")
    r = generate_parser(CODE, YEAR, golden_entry, base_fn, OUT, max_rounds=8)

    print(f"\n{'='*56}")
    if r["accepted"]:
        print(f"🎉 完全正确！第 {r['rounds']} 轮达到 exact（分=1.0），认证收下")
        print(f"   LLM 写的解析器: {r['out_path']}")
    else:
        print(f"🙋 {r['rounds']} 轮仍未完全正确（最好 {r.get('best_score')}）→ 转人工")
        print(f"   不留半成品；该版式交人工处理/补 fork 母本/换强模型")


if __name__ == "__main__":
    main()
