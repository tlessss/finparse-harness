"""
LLM 客户端 — 按角色调模型（构建期用）

现状全用 DeepSeek（OpenAI 兼容，config.py 配置）。设计上 codegen 角色该用强模型
（理想 Claude Opus 4.8，走 anthropic SDK）；将来拿到 key 只换这一处。

用法：
  from src.agents.llm_client import chat
  text = chat([{"role":"user","content":"..."}], role="codegen")
"""

from typing import List, Dict

from src.config import Config


def chat(messages: List[Dict], role: str = "codegen",
         temperature: float = 0.2, max_tokens: int = 4000,
         model: str = None) -> str:
    """一次对话补全，返回文本。model 由调用方按 agent 传入（resolve_model(agent_id)）；
    缺省回退 Config.LLM_MODEL。role 保留作日志/兼容，不再决定模型。"""
    from openai import OpenAI       # 延迟导入，未装也不影响纯规则路径
    client = OpenAI(api_key=Config.LLM_API_KEY, base_url=Config.LLM_BASE_URL)
    resp = client.chat.completions.create(
        model=model or Config.LLM_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""
