"""自愈管线决策单测 — mock 路由/修复，不碰 PDF/LLM。"""

import os
import sys
_LEGACY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _LEGACY)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "."))

import heal_pipeline as hp


def test_routed_uses_certified(monkeypatch):
    monkeypatch.setattr(hp, "route_field",
                        lambda spec, c, y: {"status": "routed", "parser_key": "K",
                                            "result": {"industries": []}, "signal": {}})
    r = hp.heal_revenue("X", 2025)
    assert r["action"] == "routed" and r["status"] == "ok" and r["parser_key"] == "K"


def test_no_golden_escalates(monkeypatch):
    monkeypatch.setattr(hp, "route_field", lambda spec, c, y: {"status": "needs_repair"})
    r = hp.heal_revenue("X", 2025, golden_entry=None)
    assert r["action"] == "escalate" and r["status"] == "needs_human"


def test_repair_exact_certifies(monkeypatch):
    monkeypatch.setattr(hp, "route_field", lambda spec, c, y: {"status": "needs_repair"})
    monkeypatch.setattr(hp, "repair", lambda *a, **k: {
        "accepted": True, "action": "new", "parser": "p.py", "score": 1.0, "rounds": 2})
    seen = []
    monkeypatch.setattr(hp, "certify",
                        lambda key, path, field=None, fingerprints=None: seen.append((key, path, field)))
    r = hp.heal_revenue("X", 2025, golden_entry={"revenue_breakdown": {}})
    assert r["status"] == "certified" and r["action"] == "new"
    assert seen and seen[0][1] == "p.py"                 # 认证入目录被调用
    assert seen[0][2] == "revenue_breakdown"             # 按字段认证


def test_repair_stuck_escalates(monkeypatch):
    monkeypatch.setattr(hp, "route_field", lambda spec, c, y: {"status": "needs_repair"})
    monkeypatch.setattr(hp, "repair", lambda *a, **k: {
        "accepted": False, "best_score": 0.44, "rounds": 8})
    monkeypatch.setattr(hp, "certify", lambda *a, **k: None)
    r = hp.heal_revenue("X", 2025, golden_entry={"revenue_breakdown": {}})
    assert r["status"] == "needs_human" and r["action"] == "escalate"


def test_heal_field_cost_certifies_with_cost_field(monkeypatch):
    """heal_field 对成本：按 cost 字段路由 + 认证（证明自愈字段通用）。"""
    from src.eval.field_spec import COST
    monkeypatch.setattr(hp, "route_field", lambda spec, c, y: {"status": "needs_repair"})
    monkeypatch.setattr(hp, "repair", lambda *a, **k: {
        "accepted": True, "action": "new", "parser": "c.py", "score": 1.0, "rounds": 1})
    seen = []
    monkeypatch.setattr(hp, "certify",
                        lambda key, path, field=None, fingerprints=None: seen.append(field))
    r = hp.heal_field(COST, "X", 2025, golden_entry={"cost_breakdown": []})
    assert r["status"] == "certified" and seen and seen[0] == "cost_breakdown"
