"""统一配置 — 从 .env 读取"""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Config:
    # ── 数据库（复用 caibaoxia） ──
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://tless:Tjhwn30998k@main-tless.mysql.polardb.rds.aliyuncs.com:3306/caibaoxia",
    )

    # ── LLM ──
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")

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
