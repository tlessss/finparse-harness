"""分诊队列 — 纯函数单测(隔离临时文件)。"""
import src.eval.triage_queue as tq


def test_enqueue_dedup_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(tq, "_QUEUE", str(tmp_path / "q.json"))
    tq.enqueue("000425", 2025, "revenue_breakdown", "needs_write")
    tq.enqueue("000425", 2025, "revenue_breakdown", "needs_write")        # 去重
    assert len(tq.list_open()) == 1
    tq.enqueue("300005", 2025, "cost_breakdown", "low_confidence",
               {"confidence": "low", "clean": True})
    assert len(tq.list_open()) == 2
    assert len(tq.list_open(reason="low_confidence")) == 1
    assert tq.resolve("000425", 2025, "revenue_breakdown")
    assert len(tq.list_open()) == 1
    s = tq.summary()
    assert s["open"] == 1 and s["by_reason"]["low_confidence"] == 1


def test_reopen_after_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(tq, "_QUEUE", str(tmp_path / "q.json"))
    tq.enqueue("X", 2025, "rnd_info", "needs_write")
    tq.resolve("X", 2025, "rnd_info")
    assert tq.list_open() == []
    tq.enqueue("X", 2025, "rnd_info", "suspicious")                       # 又出问题→重开
    assert len(tq.list_open()) == 1 and tq.list_open()[0]["reason"] == "suspicious"


def test_ok_and_coverage(tmp_path, monkeypatch):
    """覆盖台账：ok(绿)与 open(红橙)都记，summary 给覆盖率。"""
    monkeypatch.setattr(tq, "_QUEUE", str(tmp_path / "q.json"))
    tq.record_ok("000425", 2025, "revenue_breakdown", {"confidence": "high"})
    tq.record_ok("300005", 2025, "cost_breakdown", {"confidence": "unknown"})
    tq.enqueue("300005", 2025, "revenue_breakdown", "needs_write")
    s = tq.summary()
    assert s["total"] == 3 and s["ok"] == 2 and s["open"] == 1
    assert s["coverage_pct"] == round(2 / 3 * 100, 1)
    assert len(tq.list_ok()) == 2 and len(tq.list_open()) == 1
    # 同字段从 open 变 ok（修好后）→ 翻成可信
    tq.record_ok("300005", 2025, "revenue_breakdown", {"confidence": "high"})
    assert tq.summary()["ok"] == 3 and tq.summary()["open"] == 0
