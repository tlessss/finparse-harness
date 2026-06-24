"""自愈管线决策单测 — mock 路由/修复，不碰 PDF/LLM。"""

import src.agents.heal_pipeline as hp


def test_routed_uses_certified(monkeypatch):
    monkeypatch.setattr(hp, "route_revenue",
                        lambda c, y: {"status": "routed", "parser_key": "K",
                                      "result": {"industries": []}, "signal": {}})
    r = hp.heal_revenue("X", 2025)
    assert r["action"] == "routed" and r["status"] == "ok" and r["parser_key"] == "K"


def test_no_golden_escalates(monkeypatch):
    monkeypatch.setattr(hp, "route_revenue", lambda c, y: {"status": "needs_repair"})
    r = hp.heal_revenue("X", 2025, golden_entry=None)
    assert r["action"] == "escalate" and r["status"] == "needs_human"


def test_repair_exact_certifies(monkeypatch):
    monkeypatch.setattr(hp, "route_revenue", lambda c, y: {"status": "needs_repair"})
    monkeypatch.setattr(hp, "repair", lambda *a, **k: {
        "accepted": True, "action": "new", "parser": "p.py", "score": 1.0, "rounds": 2})
    seen = []
    monkeypatch.setattr(hp, "certify", lambda key, path: seen.append((key, path)))
    r = hp.heal_revenue("X", 2025, golden_entry={"revenue_breakdown": {}})
    assert r["status"] == "certified" and r["action"] == "new"
    assert seen and seen[0][1] == "p.py"        # 认证入目录被调用


def test_repair_stuck_escalates(monkeypatch):
    monkeypatch.setattr(hp, "route_revenue", lambda c, y: {"status": "needs_repair"})
    monkeypatch.setattr(hp, "repair", lambda *a, **k: {
        "accepted": False, "best_score": 0.44, "rounds": 8})
    monkeypatch.setattr(hp, "certify", lambda *a: None)
    r = hp.heal_revenue("X", 2025, golden_entry={"revenue_breakdown": {}})
    assert r["status"] == "needs_human" and r["action"] == "escalate"
