"""自愈"改规则"工具集 — 受控编辑 parser_rules/*.yaml，治规则级 bug。

设计原则（让弱模型也改不坏）：
  ① 校验参数（非法直接拒绝）  ② 幂等（已存在同映射→noop）  ③ 冲突拒绝（已映射别的→不覆盖）
  ④ 外科式文本插入（只加一行，保留注释/格式，不重写整个 YAML）  ⑤ 写后清规则缓存（立即生效）

第一个工具：add_section_marker —— 往 dimensions 加切桶标记，治"维度没识别/翻倍"(dim_leak)。
"""

import re

from src.parsers.infra.rule_loader import load_rule, clear_cache, _RULES_DIR

_VALID_DIMS = {"industries", "segments", "regions", "by_channel"}


def _find_block(lines, key):
    """找 "  <key>:" 这一行（缩进的块键）的行号。"""
    for i, line in enumerate(lines):
        if re.match(rf"^\s+{re.escape(key)}:\s*$", line):
            return i
    return None


def _block_end(lines, di):
    """从块键行 di 往下，找最后一条条目行；返回(插入位置, 条目缩进)。
    遇到缩进回退到块键层、或注释行、或空行，即认为块结束。"""
    key_indent = len(lines[di]) - len(lines[di].lstrip())
    entry_indent, last = None, di
    for i in range(di + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            break
        ind = len(line) - len(line.lstrip())
        if ind <= key_indent or line.lstrip().startswith("#"):
            break
        entry_indent, last = ind, i
    indent = " " * (entry_indent if entry_indent is not None else key_indent + 2)
    return last + 1, indent


def add_section_marker(text: str, dim: str, field: str = "revenue") -> dict:
    """往 <field>.yaml 的 dimensions 加一条切桶标记 text→dim。

    Returns: {ok, action(added|noop|conflict), message, file?, line?}
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "message": "text 不能为空"}
    if dim not in _VALID_DIMS:
        return {"ok": False, "message": f"dim 须是 {sorted(_VALID_DIMS)} 之一，收到 {dim!r}"}

    rule = load_rule(field, reload=True) or {}
    cur = rule.get("revenue_breakdown", {}).get("dimensions") or {}
    if text in cur:
        if cur[text] == dim:
            return {"ok": True, "action": "noop", "message": f"已存在 {text}→{dim}，无需改"}
        return {"ok": False, "action": "conflict",
                "message": f"{text} 已映射到 {cur[text]}，拒绝改成 {dim}（冲突，需人工确认）"}

    path = _RULES_DIR / f"{field}.yaml"
    if not path.exists():
        return {"ok": False, "message": f"找不到 {path}"}
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    di = _find_block(lines, "dimensions")
    if di is None:
        return {"ok": False, "message": f"{field}.yaml 没找到 dimensions 段"}
    at, indent = _block_end(lines, di)
    lines.insert(at, f"{indent}{text}: {dim}\n")
    path.write_text("".join(lines), encoding="utf-8")
    clear_cache()
    return {"ok": True, "action": "added", "message": f"已加切桶标记 {text}→{dim}",
            "file": str(path), "line": at + 1}
