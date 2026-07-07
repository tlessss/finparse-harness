"""Agent 管理页后端 — agent 清单/详情/存模板/模型路由。

数据源：模板型 agent = src/prompts/templates/*.yaml（可看/编辑/配模型/部分可试跑）。
        codegen 已抽进 templates/codegen.yaml（不再是无模板内联 agent）。
"""

from typing import Any, Dict, Optional

from src.config import Config
from src.prompts.registry import list_agents, list_template_ids, load_template, save_template
from src.agents.llm_routing import AGENT_IDS, resolve_model, routing_matrix, save_routing

# 无模板 agent（prompt 仍内联在代码、暂不支持在线编辑）——codegen 已迁入模板,此处清空
_INLINE: Dict[str, Dict[str, str]] = {}
# agent_id → 现有 debug 试跑端点前缀（/debug/{prefix}/prepare|chat）
_PLAYGROUND: Dict[str, str] = {"judge": "judge", "verify": "verify", "diagnose": "heal"}


def agents_list() -> Dict[str, Any]:
    """全部 agent 元信息 + 当前模型（供列表页）。"""
    items = []
    for a in list_agents():
        aid = a["id"]
        items.append({**a, "model": resolve_model(aid), "has_template": True,
                      "has_playground": aid in _PLAYGROUND, "playground": _PLAYGROUND.get(aid)})
    for aid, meta in _INLINE.items():
        items.append({"id": aid, "version": "-", "role": meta["role"], "model": resolve_model(aid),
                      "has_template": False, "has_playground": False, "note": meta["note"]})
    return {"agents": items, "default_model": Config.LLM_MODEL}


def agent_detail(agent_id: str) -> Dict[str, Any]:
    """单个 agent 详情：模板型给 system/user/output_schema，无模板给内联说明。"""
    if agent_id in _INLINE:
        return {"id": agent_id, "role": _INLINE[agent_id]["role"], "model": resolve_model(agent_id),
                "has_template": False, "has_playground": False, "note": _INLINE[agent_id]["note"]}
    if agent_id not in list_template_ids():
        return {"error": f"未知 agent: {agent_id}"}
    tpl = load_template(agent_id)
    return {"id": agent_id, "version": tpl.get("version", "v1"), "role": tpl.get("role", "judge"),
            "system": tpl.get("system") or "", "user": tpl.get("user") or "",
            "output_schema": tpl.get("output_schema"), "model": resolve_model(agent_id),
            "has_template": True, "has_playground": agent_id in _PLAYGROUND,
            "playground": _PLAYGROUND.get(agent_id)}


def agent_save(agent_id: str, system: str, user: str, version: Optional[str] = None) -> Dict[str, Any]:
    """回写模板（含写前自校验）。返回保存后的 system/user/version 或 error。"""
    try:
        tpl = save_template(agent_id, system, user, version=version or None)
    except ValueError as e:
        return {"error": str(e)}
    return {"ok": True, "id": agent_id, "version": tpl.get("version"),
            "system": tpl.get("system"), "user": tpl.get("user")}


def routing_get() -> Dict[str, Any]:
    """全部 agent 当前模型矩阵 + 下拉候选 + 默认模型。"""
    return {"models": routing_matrix(), "available": Config.LLM_AVAILABLE_MODELS,
            "default": Config.LLM_MODEL}


def routing_set(agent_id: str, model: str) -> Dict[str, Any]:
    """设/清某 agent 的模型覆盖（model 空或等于默认 → 回退默认）。"""
    if agent_id not in AGENT_IDS:
        return {"error": f"未知 agent: {agent_id}"}
    save_routing(agent_id, model or "")
    return {"models": routing_matrix()}
