import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings:
    # 数据库：优先 PostgreSQL，回退 SQLite
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{os.path.join(BASE_DIR, 'test.db')}")

    # 连接池配置（PostgreSQL 生效）
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "3600"))

    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_MINUTES", str(60 * 24 * 7)))  # 7 天
    ACTIVATION_TOKEN_EXPIRE_MINUTES: int = 10
    # 密码哈希成本（rounds 越高越慢越安全；60000 约 60ms，兼顾安全与登录性能）
    PASSWORD_HASH_ROUNDS: int = int(os.getenv("PASSWORD_HASH_ROUNDS", "60000"))

    MAIL_HOST: str = os.getenv("MAIL_HOST", "smtp.163.com")
    MAIL_PORT: int = 465
    MAIL_USERNAME: str = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD", "")
    MAIL_FROM: str = os.getenv("MAIL_FROM", "")

    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    SILICONFLOW_API_KEY: str = os.getenv("SILICONFLOW_API_KEY", "")
    # 仅本地压测可选；生产勿设置，设置后可用该 Bearer 调 /rag/* 而无需 JWT
    RAG_BENCHMARK_TOKEN: str = os.getenv("RAG_BENCHMARK_TOKEN", "")
    # 手动触发 /rag/rebuild（重建 cosine 集合并灌库）的管理 token；不配则接口禁用
    RAG_REBUILD_TOKEN: str = os.getenv("RAG_REBUILD_TOKEN", "")

    BASE_URL: str = os.getenv("BASE_URL", "https://baisonghao.website")
    ENV: str = os.getenv("ENV", "dev")
    ENABLE_DEMO_ACCOUNT: bool = os.getenv("ENABLE_DEMO_ACCOUNT", "false").lower() == "true"

    REDIS_URL: str = os.getenv("REDIS_URL", "")
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")


settings = Settings()
