"""抽表阶段 LLM 判定 — 解析前先判候选表对不对。

抽表错了后面全错(garbage in garbage out)。两类错最常见：
  ① 挑错表：把毛利率表/无关表当成营收构成表
  ② 抽错位：pdfplumber 串列/合并单元格/行错位，结构烂掉
锚验证只能事后说"结果不对",说不出"为啥不对";这里在解析前就给出诊断(错表/错位/缺列)。
"""

import json
import re
from typing import Optional

from src.agents.llm_client import chat
from src.agents.llm_routing import resolve_model
from src.prompts.registry import build_messages


def _serialize(grid, max_rows=30) -> str:
    out = []
    for row in grid[:max_rows]:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            out.append(" | ".join(c for c in cells if c))
    return "\n".join(out)


def _extract_json(raw: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    txt = (m.group(1) if m else raw).strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i >= 0 and j > i:
        txt = txt[i:j + 1]
    try:
        return json.loads(txt)
    except Exception:
        return None


def judge_extraction(spec, table: dict, year: int, log=print) -> Optional[dict]:
    """LLM 判一张候选表：是不是目标表 + 抽取是否干净。返回 verdict / None(LLM 异常)。
    verdict = {is_target, clean, issue, confidence}。"""
    grid = (table or {}).get("table") or []
    text = _serialize(grid)
    if not text:
        return {"is_target": False, "clean": False, "issue": "空表", "confidence": "high"}
    messages = build_messages("extract_judge", {
        "label": spec.label, "spec_note": spec.spec_note, "year": year, "table_text": text,
    })["messages"]
    try:
        raw = chat(messages, role="judge", temperature=0, model=resolve_model("extract_judge"))
    except Exception as e:
        log(f"    抽表判定异常: {str(e)[:60]}")
        return None
    return _extract_json(raw)
