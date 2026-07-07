"""
LLM 客户端 — 按角色调模型（构建期用）

现状全用 DeepSeek（OpenAI 兼容，config.py 配置）。设计上 codegen 角色该用强模型
（理想 Claude Opus 4.8，走 anthropic SDK）；将来拿到 key 只换这一处。

用法：
  from src.agents.llm_client import chat
  text = chat([{"role":"user","content":"..."}], role="codegen")
"""

from typing import List, Dict, Tuple

from src.config import Config


def _provider_for(model: str) -> Tuple[str, str]:
    """按模型名前缀选 provider(base_url, api_key)：
    qwen* → DashScope(阿里百炼)；其余 → 默认 endpoint(DeepSeek)。
    让『只给 codegen 配强代码模型 qwen3-coder-plus、其余 agent 留 DeepSeek』只需在路由里换模型名即可。"""
    m = (model or "").lower()
    if m.startswith("qwen"):
        return Config.DASHSCOPE_BASE_URL, Config.DASHSCOPE_API_KEY
    return Config.LLM_BASE_URL, Config.LLM_API_KEY


def chat(messages: List[Dict], role: str = "codegen",
         temperature: float = 0.2, max_tokens: int = 4000,
         model: str = None) -> str:
    """一次对话补全，返回文本。model 由调用方按 agent 传入（resolve_model(agent_id)）；
    缺省回退 Config.LLM_MODEL。base_url/key 按模型名自动选 provider（_provider_for）。"""
    from openai import OpenAI       # 延迟导入，未装也不影响纯规则路径
    mdl = model or Config.LLM_MODEL
    base_url, api_key = _provider_for(mdl)
    client = OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=mdl,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""
