"""
代码沙箱执行 (M4) — 把"一个解析器版本(.py)"变成可打分的 parse_fn

专用解析器契约（LLM / 人 写的每个版本都实现它）：
    def parse(tables, context=None) -> revenue_breakdown
      tables: scan_pdf 形状的列表（含 page/table/cell_bbox…），来自抽表缓存
      返回:   {"industries":[{name,revenue_yuan,ratio_pct}], "segments":[...], "regions":[...]}

本模块：加载某版本文件 → 用抽表缓存喂它 → 包成 parse_fn(code,year)，
直接丢进 run_eval 打分 / accept_candidate 把关。这就是"让 LLM 改解析器"的执行端。

⚠️ 隔离强度：当前用 importlib 在本进程 exec（适合可信/自己写的版本）。
   跑任意 LLM 代码需加 subprocess + 资源/超时限制——列为硬化 TODO，不阻塞闭环打通。

用法：
  from src.eval.sandbox_exec import version_parse_fn
  fn = version_parse_fn("src/parsers/versions/rev_000425_v1.py")
  rb = fn("000425", 2025)         # 在缓存表上跑该版本
"""

import importlib.util

from src.eval.table_cache import get_tables


def load_parser(path: str):
    """从 .py 文件加载 parse(tables, context=None) 函数。"""
    spec = importlib.util.spec_from_file_location("bespoke_parser", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "parse"):
        raise ValueError(f"{path} 缺少 parse(tables) 函数（不符合专用解析器契约）")
    return mod.parse


def version_parse_fn(path: str):
    """把版本文件包成 parse_fn(code,year) -> revenue_breakdown（跑在抽表缓存上）。"""
    parse = load_parser(path)

    def fn(code: str, year: int):
        tables = get_tables(code, year)
        if tables is None:
            return None
        return parse(tables)        # 异常向上抛，由 eval_version 记为 error
    return fn
