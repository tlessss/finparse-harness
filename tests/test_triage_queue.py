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
