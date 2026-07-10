"""子进程沙箱入口 —— 隔离跑 LLM 现写的解析器，防死循环/崩溃/恶意代码拖垮主进程。

由 sandbox_exec.version_parse_fn_sandboxed 以**子进程**方式调起：
    python -m src.eval.sandbox_runner <parser_path> <code> <year>
在抽表缓存上跑该解析器的 parse(tables)，结果以单行 `__SBX__<json>` 写到 stdout。
异常照常抛（子进程非 0 退出 / stderr），由父进程记为 error，不影响主进程。
"""

import json
import sys


def main() -> None:
    if len(sys.argv) < 4:
        sys.stderr.write("usage: sandbox_runner <parser_path> <code> <year>")
        sys.exit(2)
    path, code, year = sys.argv[1], sys.argv[2], int(sys.argv[3])
    from src.eval.table_cache import get_tables
    from src.eval.sandbox_exec import load_parser
    tables = get_tables(code, year)
    result = None if tables is None else load_parser(path)(tables)
    sys.stdout.write("__SBX__" + json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
