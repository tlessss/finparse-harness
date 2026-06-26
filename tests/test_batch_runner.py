"""批量跑批器 — 控制/进度文件逻辑单测(不跑引擎)。"""
import src.batch_runner as br


def test_control_and_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "_STATE", str(tmp_path / "b.json"))
    assert br.progress()["running"] is False                 # 无状态文件
    br._write({"running": True, "done": 2, "total": 5})
    assert br.progress()["done"] == 2
    br.control("pause")
    assert br._read()["paused"] is True
    br.control("resume")
    assert br._read()["paused"] is False
    br.control("stop")
    assert br._read()["stopped"] is True and br._read()["paused"] is False
