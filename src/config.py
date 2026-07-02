"""统一配置 — 从 .env 读取"""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Config:
    # ── 数据库（复用 caibaoxia）—— 凭据只从 .env 读取，不在代码里硬编码 ──
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    # 解析结果写入/读取的目标表。**默认安全走测试镜像表**（当前阶段"不碰生产"），
    # 所有读写打到 financial_reports_test；真要写生产须显式 REPORTS_TABLE=financial_reports。
    REPORTS_TABLE: str = os.getenv("REPORTS_TABLE", "financial_reports_test")
    # 复核 agent 判 pass 后是否自动入库(写 REPORTS_TABLE,留痕 source=verify_agent)。
    # True=自动入库(测试库阶段);设 False 则 pass 只进 commit 队列 pending,等 ⑤ 人审通过才入库。
    AUTO_COMMIT_ON_VERIFY: bool = os.getenv("AUTO_COMMIT_ON_VERIFY", "true").lower() != "false"

    # ── LLM ──
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    # 按单个 agent 覆盖模型的持久化文件（管理页写，llm_routing 读）；缺省全部回退 LLM_MODEL。
    AGENT_ROUTING_FILE: Path = Path(os.getenv("AGENT_ROUTING_FILE", str(ROOT / "goldset" / "llm_routing.json")))
    # 管理页模型下拉候选（也允许自由填）。注：本期路由只切同一 OpenAI 兼容 endpoint 下的 model 字符串。
    LLM_AVAILABLE_MODELS = [
        m for m in os.getenv(
            "LLM_AVAILABLE_MODELS",
            "deepseek-chat,deepseek-reasoner,claude-opus-4-8,claude-sonnet-5",
        ).split(",") if m.strip()
    ]

    # ── PDF 缓存（复用 book-agent） ──
    PDF_CACHE_DIR: Path = Path(
        os.getenv("PDF_CACHE_DIR", str(ROOT.parent / "book-agent" / "output" / "pdf_cache"))
    )

    # ── 向量库（复用 quantification 存量资产） ──
    RAG_DATA_DIR: Path = Path(
        os.getenv("RAG_DATA_DIR", str(Path.home() / "formal" / "quantification" / "rag_data"))
    )
    RAG_MODEL_DIR: Path = Path(
        os.getenv(
            "RAG_MODEL_DIR",
            str(Path.home() / "formal" / "quantification" / "rag_models" / "BAAI" / "bge-small-zh-v1.5"),
        )
    )

    # ── 服务端口 ──
    PORT: int = int(os.getenv("PORT", "8200"))
    FRONTEND_PORT: int = int(os.getenv("FRONTEND_PORT", "5281"))

    # ── 迭代控制 ──
    MAX_ITERATE: int = int(os.getenv("MAX_ITERATE", "3"))
    LAYOUT_SIMILARITY_THRESHOLD: float = float(os.getenv("LAYOUT_SIMILARITY_THRESHOLD", "0.80"))
    SEMANTIC_SIMILARITY_THRESHOLD: float = float(os.getenv("SEMANTIC_SIMILARITY_THRESHOLD", "0.60"))
