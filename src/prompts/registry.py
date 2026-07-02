"""Prompt Registry — agent_id → YAML 模板 + 渲染。"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.prompts.render import render_template

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=32)
def load_template(agent_id: str) -> Dict[str, Any]:
    path = _TEMPLATES_DIR / f"{agent_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if data.get("id") != agent_id:
        data["id"] = agent_id
    return data


def list_template_ids() -> List[str]:
    """templates/ 下所有 agent_id（文件名去 .yaml）。"""
    return sorted(p.stem for p in _TEMPLATES_DIR.glob("*.yaml"))


def list_agents() -> List[Dict[str, Any]]:
    """枚举全部模板型 agent 的元信息（供管理页列表，不含正文全文）。"""
    out: List[Dict[str, Any]] = []
    for aid in list_template_ids():
        tpl = load_template(aid)
        out.append({
            "id": aid,
            "version": tpl.get("version", "v1"),
            "role": tpl.get("role", "judge"),
            "has_system": bool(tpl.get("system")),
            "has_user": bool(tpl.get("user")),
            "output_schema": tpl.get("output_schema"),
        })
    return out


def _emit_block(key: str, text: str) -> str:
    """把 system/user 渲染成块标量：每行缩进 2 空格，空行留空。块标量会剥掉这 2 空格 →
    正文逐字保真（含行内前导空格）。按尾部换行数选 chomping：0→'|-'、1→'|'、多→'|+'。"""
    if not text:
        return f'{key}: ""\n'
    stripped = text.rstrip("\n")
    trailing = len(text) - len(stripped)
    chomp = "-" if trailing == 0 else ("" if trailing == 1 else "+")
    body = "\n".join(("  " + ln) if ln else "" for ln in stripped.split("\n"))
    out = f"{key}: |{chomp}\n{body}\n"
    if trailing > 1:                       # |+ 保留多余尾换行
        out += "\n" * (trailing - 1)
    return out


def _emit_output_schema(schema: Any) -> str:
    if schema is None:
        return ""
    dumped = yaml.safe_dump(schema, allow_unicode=True, default_flow_style=False, sort_keys=False)
    indented = "".join(("  " + ln + "\n") for ln in dumped.rstrip("\n").split("\n"))
    return "output_schema:\n" + indented


def _dump_template(data: Dict[str, Any]) -> str:
    return (
        f"id: {data['id']}\n"
        f"version: {data.get('version', 'v1')}\n"
        f"role: {data.get('role', 'judge')}\n"
        + _emit_block("system", data.get("system") or "")
        + _emit_block("user", data.get("user") or "")
        + _emit_output_schema(data.get("output_schema"))
    )


def save_template(agent_id: str, system: str, user: str,
                  version: Optional[str] = None, output_schema: Any = None) -> Dict[str, Any]:
    """回写模板到 templates/{agent_id}.yaml 并清缓存热生效。
    只允许已存在的 agent_id（防路径穿越）；写盘前做 system/user 往返自校验，杜绝块标量走样。"""
    if agent_id not in list_template_ids():
        raise ValueError(f"unknown agent template: {agent_id}")
    cur = load_template(agent_id)
    data = {
        "id": agent_id,
        "version": version or cur.get("version", "v1"),
        "role": cur.get("role", "judge"),
        "system": system,
        "user": user,
        "output_schema": output_schema if output_schema is not None else cur.get("output_schema"),
    }
    text = _dump_template(data)
    parsed = yaml.safe_load(text) or {}
    if parsed.get("system") != system or parsed.get("user") != user:
        raise ValueError("模板序列化自校验失败（system/user 往返不一致），未写盘")
    with open(_TEMPLATES_DIR / f"{agent_id}.yaml", "w", encoding="utf-8") as f:
        f.write(text)
    load_template.cache_clear()
    return load_template(agent_id)


def build_messages(agent_id: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """加载模板、渲染 system/user，返回统一契约。"""
    tpl = load_template(agent_id)
    system = render_template(tpl.get("system") or "", variables)
    user = render_template(tpl.get("user") or "", variables)
    messages: List[Dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system})
    if user.strip():
        messages.append({"role": "user", "content": user})
    return {
        "agent_id": agent_id,
        "version": tpl.get("version", "v1"),
        "role": tpl.get("role", "judge"),
        "messages": messages,
        "output_schema": tpl.get("output_schema"),
    }
