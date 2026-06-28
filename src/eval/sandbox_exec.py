"""
代码沙箱执行 (M4) — 把"一个解析器版本文件(.py)"变成可调用的解析函数
====================================================================

注册表里每个专用解析器 = 磁盘上一个 .py 文件。本模块负责：
  加载这个文件 → 拿到它的 parse() → 喂"抽表缓存"里的表 → 返回结果。
这就是"构建期 LLM 写解析器、运行期跑冻结的确定性代码"里 **运行期那一端**。

专用解析器契约(每个版本文件都要实现)：
    def parse(tables, context=None) -> 字段结果
      tables: scan_pdf 形状的表列表(含 page/table/cell_bbox)，来自抽表缓存
      返回:   如营收 {"industries":[{name,revenue_yuan,ratio_pct}], "segments":[...], "regions":[...]}

⚠️ 隔离强度(诚实提醒)：当前用 importlib 在**本进程内 exec** 加载执行——
   对"自己/可信写的版本"OK；要跑 LLM 现写的任意代码，需加 subprocess + 超时/资源限制
   (恶意或死循环代码会拖垮主进程)。这是硬化 TODO，不阻塞闭环打通。

用法：
  fn = version_parse_fn("src/parsers/versions/rev_000425_v1.py")
  rb = fn("000425", 2025)         # 在缓存表上跑该版本
"""

import importlib.util

from src.eval.table_cache import get_tables


def load_parser(path: str):
    """从一个 .py 文件按路径动态加载，返回它的 parse 函数。

    importlib 这三步 = "把任意路径的 py 文件当模块加载执行"，
    所以注册表里一堆解析器代码文件能即插即用地跑起来。
    """
    spec = importlib.util.spec_from_file_location("bespoke_parser", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                 # 真正执行该文件，得到模块对象
    if not hasattr(mod, "parse"):               # 契约门禁：必须有 parse()
        raise ValueError(f"{path} 缺少 parse(tables) 函数（不符合专用解析器契约）")
    return mod.parse


def version_parse_fn(path: str):
    """把版本文件包成 fn(code, year) -> 结果（跑在抽表缓存上）。

    返回的是闭包：记住了加载好的 parse，之后只要给 (code, year)，
    就从缓存取表 → 喂给 parse → 出结果。route_field 里就是这么逐个跑候选的。
    """
    parse = load_parser(path)                    # 先加载(只加载一次)

    def fn(code: str, year: int):
        tables = get_tables(code, year)          # 从抽表缓存拿这份报告的表
        if tables is None:
            return None
        return parse(tables)                     # 跑解析器；异常向上抛，由调用方记为 error
    return fn
