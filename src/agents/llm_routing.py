"""按单个 agent 的模型路由 — agent_id → model 覆盖，缺省回退 Config.LLM_MODEL。

管理页写 goldset/llm_routing.json（形如 {"codegen":"claude-opus-4-8"}），llm_client.chat 读。
本期只切**同一 OpenAI 兼容 endpoint 下的 model 字符串**；跨供应商（不同 base_url/key）作为后续小改，
届时可把值从纯字符串扩成 {"model":...,"base_url":...,"api_key_env":...}。
"""

import json
from typing import Dict, Optional

from src.config import Config

# 管理页会列出的全部 agent（模板型 5 个 + 无模板的 codegen）。
AGENT_IDS = ["judge", "verify", "extract_judge", "auto_heal", "diagnose", "codegen", "select_table", "rule_heal"]

_cache: Optional[Dict[str, str]] = None


def load_routing() -> Dict[str, str]:
    """读 routing.json（模块级缓存）。文件不存在/损坏 → 空表（全部走默认）。"""
    global _cache
    if _cache is None:
        try:
            with open(Config.AGENT_ROUTING_FILE, encoding="utf-8") as f:
                data = json.load(f)
            _cache = {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
        except Exception:
            _cache = {}
    return _cache


def resolve_model(agent_id: str) -> str:
    """该 agent 当前用的模型：routing 覆盖 > Config.LLM_MODEL 默认。"""
    return load_routing().get(agent_id) or Config.LLM_MODEL


def save_routing(agent_id: str, model: str) -> Dict[str, str]:
    """设/清某 agent 的模型覆盖并落盘 + 清缓存。model 传空/等于默认 → 删除该覆盖（回退默认）。"""
    global _cache
    routing = dict(load_routing())
    if model and model != Config.LLM_MODEL:
        routing[agent_id] = model
    else:
        routing.pop(agent_id, None)
    Config.AGENT_ROUTING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(Config.AGENT_ROUTING_FILE, "w", encoding="utf-8") as f:
        json.dump(routing, f, ensure_ascii=False, indent=2)
    _cache = routing
    return routing


def routing_matrix() -> Dict[str, str]:
    """全部 agent 的当前模型（含回退到默认的）。供管理页展示。"""
    return {aid: resolve_model(aid) for aid in AGENT_IDS}
