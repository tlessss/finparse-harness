"""judge/verify/extract_judge/auto_heal Prompt 工程化回归 — 模板契约 + 防漂移关键语。

这四个 agent 的 prompt 从内联 f-string 迁到 templates/*.yaml。本测试锁住：
① 每个模板都有 id/version/system/user/output_schema；② build_messages 产出 system+user 两条；
③ 最易漂移的约束语（复核的 ⚠ 边界、judge 的错误分类、单位/年份护栏）不被误删。
"""

import unittest

from src.prompts.registry import build_messages, load_template

_AGENTS = ["judge", "verify", "extract_judge", "auto_heal"]


class TestTemplateContract(unittest.TestCase):
    def test_all_templates_load_with_schema(self):
        for aid in _AGENTS:
            tpl = load_template(aid)
            self.assertEqual(tpl["id"], aid)
            self.assertEqual(tpl.get("version"), "v1", f"{aid} 缺 version")
            self.assertTrue(tpl.get("system"), f"{aid} 缺 system")
            self.assertTrue(tpl.get("user"), f"{aid} 缺 user")
            self.assertTrue(tpl.get("output_schema"), f"{aid} 缺 output_schema")

    def test_build_messages_two_roles(self):
        for aid in _AGENTS:
            built = build_messages(aid, {})
            roles = [m["role"] for m in built["messages"]]
            self.assertEqual(roles, ["system", "user"], f"{aid} messages 角色异常")


class TestDriftGuards(unittest.TestCase):
    """把最容易在改动中被误删/改坏的关键语句钉死。"""

    def test_judge_taxonomy_and_schema(self):
        user = build_messages("judge", {})["messages"][1]["content"]
        for kw in ("unit_error", "pnl_misid", "dim_leak", "wrong_year", "name_error"):
            self.assertIn(kw, user)
        self.assertIn('"verdict":"ok|suspicious"', user)

    def test_verify_boundary_warnings(self):
        user = build_messages("verify", {})["messages"][1]["content"]
        self.assertIn("占比不用核", user)               # 占比豁免（不因缺 ratio_pct 判 hold）
        self.assertIn("绝不臆测", user)                 # 年份/期间反臆测
        self.assertIn("不要编造", user)
        self.assertIn('"verdict":"pass|hold"', user)

    def test_extract_judge_two_checks(self):
        user = build_messages("extract_judge", {})["messages"][1]["content"]
        self.assertIn("is_target", user)
        self.assertIn("clean", user)

    def test_auto_heal_unit_and_year_hints(self):
        user = build_messages("auto_heal", {})["messages"][1]["content"]
        self.assertIn("本期", user)
        self.assertIn("换算成元", user)
        self.assertIn("跳过合计", user)


class TestPlaceholderRender(unittest.TestCase):
    def test_vars_slot_in(self):
        user = build_messages("extract_judge", {
            "label": "营业收入构成", "spec_note": "分行业", "year": 2025, "table_text": "T",
        })["messages"][1]["content"]
        self.assertIn("「营业收入构成」", user)
        self.assertIn("本期是2025年", user)
        self.assertNotIn("{{label}}", user)


if __name__ == "__main__":
    unittest.main()
