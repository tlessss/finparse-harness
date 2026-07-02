"""Agent 管理页后端回归 — 模板枚举/存回保真 + 按 agent 模型路由。"""

import os
import tempfile
import unittest
from pathlib import Path

from src.config import Config
from src.prompts.registry import (build_messages, list_agents, list_template_ids,
                                  load_template, save_template)

_SAMPLE = {"grounding": "G", "source": "S", "unit_note": "", "anchor_note": "", "field": "F",
           "field_value_json": "{}", "label": "L", "spec_note": "N", "year": 2025,
           "table_text": "T", "shape": "X", "verdict": "v", "reason": "r", "dims_summary": "",
           "anchor_summary": "", "pick_meta": "", "missing_dims_text": "无", "cross_page_hint": "",
           "table_preview": "", "neighbor_tables": "(无)", "candidates": "(无)", "parse_value": "{}",
           "config_yaml": "", "parser_code": ""}


class TestTemplateRegistry(unittest.TestCase):
    def test_list_agents_covers_templates(self):
        ids = {a["id"] for a in list_agents()}
        for aid in ("judge", "verify", "extract_judge", "auto_heal", "diagnose"):
            self.assertIn(aid, ids)
        for a in list_agents():                         # 元信息齐全
            self.assertTrue(a.get("version"))
            self.assertIn("role", a)

    def test_save_template_roundtrip_byte_fidelity(self):
        """对每个模板原样存回 → build_messages 输出逐字节不变（守住 prompt 保真）。"""
        for aid in list_template_ids():
            before = build_messages(aid, _SAMPLE)
            tpl = load_template(aid)
            save_template(aid, tpl.get("system") or "", tpl.get("user") or "")
            after = build_messages(aid, _SAMPLE)
            self.assertEqual(before["messages"], after["messages"], f"{aid} messages 走样")
            self.assertEqual(before["output_schema"], after["output_schema"], f"{aid} schema 走样")

    def test_save_template_edit_takes_effect_and_clears_cache(self):
        tpl = load_template("judge")
        sys0, usr0 = tpl.get("system") or "", tpl.get("user") or ""
        try:
            save_template("judge", "临时系统词 ZZZ", usr0)
            self.assertEqual(load_template("judge")["system"], "临时系统词 ZZZ")   # 缓存已清、读到新值
        finally:
            save_template("judge", sys0, usr0)                                     # 复原
            self.assertEqual(load_template("judge")["system"], sys0)

    def test_save_template_rejects_unknown_id(self):
        for bad in ("nope", "../evil", "judge/../verify"):
            with self.assertRaises(ValueError):
                save_template(bad, "s", "u")


class TestModelRouting(unittest.TestCase):
    def setUp(self):
        import src.agents.llm_routing as R
        self._R = R
        self._orig_file = Config.AGENT_ROUTING_FILE
        self._tmp = Path(tempfile.mkdtemp()) / "llm_routing.json"
        Config.AGENT_ROUTING_FILE = self._tmp
        R._cache = None

    def tearDown(self):
        Config.AGENT_ROUTING_FILE = self._orig_file
        self._R._cache = None

    def test_default_falls_back_to_llm_model(self):
        self.assertEqual(self._R.resolve_model("judge"), Config.LLM_MODEL)

    def test_per_agent_override_isolated(self):
        self._R.save_routing("codegen", "claude-opus-4-8")
        self.assertEqual(self._R.resolve_model("codegen"), "claude-opus-4-8")
        self.assertEqual(self._R.resolve_model("judge"), Config.LLM_MODEL)   # 不影响其他 agent
        self.assertEqual(self._R.resolve_model("verify"), Config.LLM_MODEL)

    def test_clear_override_returns_to_default(self):
        self._R.save_routing("codegen", "claude-opus-4-8")
        self._R.save_routing("codegen", "")                                  # 清除
        self.assertEqual(self._R.resolve_model("codegen"), Config.LLM_MODEL)

    def test_routing_matrix_lists_all_agents(self):
        m = self._R.routing_matrix()
        self.assertEqual(set(m.keys()), set(self._R.AGENT_IDS))


if __name__ == "__main__":
    unittest.main()
