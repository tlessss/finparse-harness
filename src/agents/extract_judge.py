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
    prompt = (
        f"下面是从年报抽取的一张表格(可能挑错了表，或抽取错位)。判断两件事：\n"
        f"① is_target：它是不是「{spec.label}」要的目标表？目标口径：{spec.spec_note}\n"
        f"   （别被毛利率表/其它无关表冒充；确实是就 true）\n"
        f"② clean：抽取是否干净可解析？（列对齐、没串列、没把多列挤进一格、没行错位 = true）\n"
        f'输出 JSON：{{"is_target":true/false,"clean":true/false,"issue":"一句话问题(没问题留空)","confidence":"high/med/low"}}\n'
        f"只输出 JSON，不要解释。\n\n表格（本期是{year}年）：\n{text}"
    )
    try:
        raw = chat([{"role": "system", "content": "你审核中文年报的表格抽取质量，严谨，只输出 JSON。"},
                    {"role": "user", "content": prompt}], role="judge", temperature=0)
    except Exception as e:
        log(f"    抽表判定异常: {str(e)[:60]}")
        return None
    return _extract_json(raw)
