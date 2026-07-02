"""诊断 agent Prompt 工程化回归 — 300009 跨页 case。"""

import os
import unittest

from src.agents.diagnose_agent import prepare_diagnose
from src.prompts.registry import build_messages, load_template
from src.prompts.render import render_template


class TestPromptRender(unittest.TestCase):
    def test_render_missing_key_preserved(self):
        out = render_template("hello {{name}} {{missing}}", {"name": "world"})
        self.assertIn("world", out)
        self.assertIn("{{missing}}", out)

    def test_load_diagnose_template(self):
        tpl = load_template("diagnose")
        self.assertEqual(tpl["id"], "diagnose")
        self.assertEqual(tpl["version"], "v1")
        self.assertIn("system", tpl)
        self.assertIn("user", tpl)


class TestDiagnosePrompt300009(unittest.TestCase):
    CODE = "300009"
    YEAR = 2025
    FIELD = "revenue_breakdown"

    @classmethod
    def setUpClass(cls):
        cache = f"goldset/tables_cache/{cls.CODE}_{cls.YEAR}.json"
        if not os.path.exists(cache):
            raise unittest.SkipTest(f"需要缓存 {cache}（先对该报告跑一次 scan）")

    def test_prepare_diagnose_has_required_sections(self):
        r = prepare_diagnose(self.CODE, self.YEAR, self.FIELD)
        self.assertNotIn("error", r, r.get("error"))
        self.assertEqual(r.get("agent_id"), "diagnose")
        self.assertEqual(r.get("version"), "v1")
        msgs = r.get("messages") or []
        self.assertEqual(len(msgs), 2)
        user = msgs[1]["content"]
        for kw in (
            "生产链路选中表",
            "邻近页表片段",
            "召回/锚候选对照",
            "解析缺失维度",
            "scan_pdf拼接",
        ):
            self.assertIn(kw, user, f"missing section: {kw}")

    def test_300009_cross_page_and_missing_regions(self):
        r = prepare_diagnose(self.CODE, self.YEAR, self.FIELD)
        meta = r.get("meta") or {}
        user = (r.get("messages") or [])[1]["content"]
        # 安科生物：regions/by_channel 在 p23 续页，当前解析常缺失
        self.assertIn("regions", str(meta.get("missing_dims", [])))
        if meta.get("cross_page_suspect"):
            self.assertIn("跨页续表", user)
        self.assertIn("p23", user)  # 邻近页应出现续页

    def test_build_messages_contract(self):
        built = build_messages("diagnose", {"field": "revenue_breakdown", "verdict": "需自愈", "reason": "test",
                                            "dims_summary": "", "anchor_summary": "", "pick_meta": "",
                                            "missing_dims_text": "无", "cross_page_hint": "",
                                            "table_preview": "", "neighbor_tables": "(无)",
                                            "candidates": "(无)", "parse_value": "{}", "config_yaml": "",
                                            "parser_code": ""})
        self.assertEqual(built["agent_id"], "diagnose")
        self.assertEqual(len(built["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
