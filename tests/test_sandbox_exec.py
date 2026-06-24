"""沙箱加载器单测 — 不碰 PDF，临时写个版本文件加载执行。"""

import os
import textwrap

from src.eval.sandbox_exec import load_parser


def test_load_and_run(tmp_path):
    p = tmp_path / "ver.py"
    p.write_text(textwrap.dedent('''
        def parse(tables, context=None):
            # 假版本：把 tables 第一张表的行数塞进 industries 占位
            return {"industries": [{"name": "x", "revenue_yuan": len(tables), "ratio_pct": 100.0}]}
    '''), encoding="utf-8")
    fn = load_parser(str(p))
    out = fn([{"table": []}, {"table": []}], None)
    assert out["industries"][0]["revenue_yuan"] == 2


def test_missing_parse_rejected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("x = 1\n", encoding="utf-8")
    try:
        load_parser(str(p))
        assert False, "应当因缺 parse() 报错"
    except ValueError:
        pass
