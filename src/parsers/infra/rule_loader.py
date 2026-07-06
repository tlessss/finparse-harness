"""
规则加载器 — 规范驱动解析的规则入口（M1）

从 src/parser_rules/<field>.yaml 加载某字段的解析规则，带缓存。
规则结构见 docs/新版解析技术路线.md「四、规则 Schema」。

用法：
  from src.parsers.infra.rule_loader import load_rule
  rule = load_rule("revenue")              # 读 src/parser_rules/revenue.yaml
  aliases = rule["revenue_breakdown"]["header_aliases"]
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

import yaml

_RULES_DIR = Path(__file__).resolve().parent.parent.parent / "parser_rules"
_CACHE: Dict[str, dict] = {}


def load_rule(field: str, reload: bool = False) -> Optional[dict]:
    """
    加载某字段的规则 YAML。找不到返回 None（调用方可回退到旧逻辑）。

    Args:
        field: 规则文件名（不含扩展名），如 "revenue"
        reload: True 则忽略缓存重新读盘（优化 Agent 改规则后用）
    """
    if not reload and field in _CACHE:
        return _CACHE[field]
    path = _RULES_DIR / f"{field}.yaml"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        rule = yaml.safe_load(f) or {}
    _CACHE[field] = rule
    return rule


def clear_cache():
    _CACHE.clear()


@contextmanager
def override_rule(field: str, rule: dict):
    """临时把某字段的活动规则替换为 rule（供规则版本扫描/自愈用），退出后恢复原状。
    所有走 load_rule(field) 的读取点（营收的认列 header_aliases、切桶 dimensions，
    以及 table_recall 的召回 dimensions）都会立即看到这个覆盖——无需改动各读取点。"""
    had = field in _CACHE
    prev = _CACHE.get(field)
    _CACHE[field] = rule
    try:
        yield
    finally:
        if had:
            _CACHE[field] = prev
        else:
            _CACHE.pop(field, None)
