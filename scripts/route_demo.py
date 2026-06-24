"""
registry 路由演示 — 新报告 → 选择即验证 → 命中认证解析器就用,否则转修复

跑缓存表,秒级,无 LLM。展示:认证解析器在合适报告上自动套用,不合适不硬套。
用法: python3 -m scripts.route_demo 000425 300005 300009
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.parsers.revenue_router import route_revenue


def main():
    codes = sys.argv[1:] or ["000425", "300005", "300009"]
    for code in codes:
        r = route_revenue(code, 2025)
        tag = "✅routed" if r["status"] == "routed" else "🔧needs_repair"
        print(f"{code} {tag}  解析器={r['parser_key']}  信号={r['signal']}")
        if r["status"] == "routed":
            rb = r["result"]
            print("   " + " | ".join(f"{d}:{len(rb[d])}行" for d in rb))


if __name__ == "__main__":
    main()
