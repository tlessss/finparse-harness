"""规则版本池 — base 规则之上的一叠「delta 版本」。

设计（见 memory: 规则版本解决回归）：
  · 解析先用 **base**（src/parser_rules/<field>.yaml）。过锚就收工。
  · base 不过锚 → 逐个把版本池里的 delta 合并到 base、试解、看过锚，取第一个过锚的赢家。
  · base 优先 => 结构上不回归：能过 base 的报告根本不会去试别的版本。故不需要回归闸、不需要指纹。

版本文件：src/parser_rules/versions/<field>/*.yaml，每个是一条对 base 的增量：
  id:    唯一名（也是文件名）
  field: 规则文件名（load_rule 的键，如 "revenue"）
  note:  这条版本治什么（人看/前端看）
  delta: 只写要新增/覆盖的部分，结构与 base 同（如 delta.revenue_breakdown.dimensions）

合并语义 deep_merge：dict 递归合并；list 取并集（base 在前、去重、保序）——
  所以 delta 只需写「新增项」即可（切桶加一个标记、认列加一个别名），这正是自愈要产出的东西。
  代价：delta 无法「删」base 里的项。目前自愈只做「加覆盖」，不需要删。
"""

from pathlib import Path
from typing import Dict, List

import yaml

_VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "parser_rules" / "versions"


def deep_merge(base: dict, delta: dict) -> dict:
    """把 delta 合并到 base，返回新 dict（不改原对象）。dict 递归；list 取并集(保序去重)。"""
    if not isinstance(base, dict) or not isinstance(delta, dict):
        return delta
    out = dict(base)
    for k, v in delta.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        elif k in out and isinstance(out[k], list) and isinstance(v, list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = v
    return out


def _field_dir(field: str) -> Path:
    return _VERSIONS_DIR / field


def load_versions(field: str) -> List[Dict]:
    """读某字段的全部规则版本（按文件名排序，稳定顺序）。目录不存在 → 空列表（= 只用 base）。"""
    d = _field_dir(field)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.yaml")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = yaml.safe_load(f) or {}
        except Exception:
            continue
        if not v.get("delta"):
            continue
        v.setdefault("id", p.stem)
        out.append(v)
    return out


def merged_rule(base_rule: dict, version: dict) -> dict:
    """base 规则 + 某版本 delta → 合并后的完整规则（喂给 override_rule）。"""
    return deep_merge(base_rule or {}, version.get("delta") or {})


def save_version(field: str, version_id: str, delta: dict, note: str = "",
                 meta: dict = None) -> str:
    """把一条新版本落盘进池（供 L2 自愈把成功的规则改动固化下来）。返回文件路径。
    version_id 已存在则覆盖（自愈迭代同一版本时）。"""
    d = _field_dir(field)
    d.mkdir(parents=True, exist_ok=True)
    doc = {"id": version_id, "field": field, "note": note or "", "delta": delta}
    if meta:
        doc["meta"] = meta
    path = d / f"{version_id}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return str(path)
