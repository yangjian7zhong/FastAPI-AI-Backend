


import os
import sys

# 清除 SSL_CERT_FILE 环境变量（避免本地证书路径错误）
if "SSL_CERT_FILE" in os.environ:
    del os.environ["SSL_CERT_FILE"]
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import sqlite3
import os
import json
import sys
import time
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from loguru import logger as loguru_logger

from app.api.v1.endpoints import auth, ai
from app.core.database import AsyncSessionLocal, init_db
from app.models.user import User
from app.core.security import hash_password
from sqlalchemy import select
from app.core.redis_client import redis_client


# ---------- 同步建表（确保表存在） ----------
def ensure_db():
    db_path = os.path.join(os.path.dirname(__file__), "test.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            hashed_password VARCHAR(255) NOT NULL,
            is_active INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    print("数据库表已确认（同步建表）")

ensure_db()

# ---------- 日志配置：JSON 结构化日志（stdout）+ loguru 链路耗时 ----------
class _Utf8Stream:
    """包装 stdout/stderr：无论控制台编码（如 Windows GBK）都以 UTF-8 字节输出，避免 emoji/中文写入报错"""
    def __init__(self, stream):
        self._s = stream
    def write(self, data):
        self._s.buffer.write(str(data).encode("utf-8", "replace"))
    def flush(self):
        self._s.flush()

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)

def _json_sink(message):
    """loguru 的 JSON sink：输出到 stdout，支持 extra 字段（链路耗时等）"""
    record = message.record
    entry = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "message": record["message"],
    }
    entry.update(record["extra"])
    sys.stdout.buffer.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(_Utf8Stream(sys.stdout))])
logging.getLogger().handlers[0].setFormatter(JsonFormatter())
logger = logging.getLogger(__name__)

loguru_logger.remove()
loguru_logger.add(_json_sink)

# ---------- FastAPI 实例 ----------
app = FastAPI(
    title="FastAPI 后端接口演示",
    description="""
包含 RAG/Agent 功能模块。

## 测试账号

| 用户名 | 密码 | 说明 |
|--------|------|------|
| `demo` | `demo123` | 演示账号（已激活） |

### 使用步骤
1. 调用 `POST /api/v1/login` 获取 `access_token`
2. 点击右上角 **Authorize** 按钮，输入 `Bearer <你的token>`
3. 调用任意带锁接口

### 注册与激活
- 注册接口 `POST /api/v1/register` 会返回 `activation_link`，复制链接到浏览器打开即可激活账号
""",
    version="1.0.0",
    swagger_ui_init_oauth={
        "usePkceWithAuthorizationCodeGrant": False,
        "clientId": "swagger",
        "appName": "FastAPI 后端接口演示",
    }
)
app.mount("/docs", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "docs")), name="docs-assets")


# ========== 全局异常处理器 ==========
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"全局异常: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误: {str(exc)}"}
    )
# =====================================


# ---------- 启动事件：线程池 + 自动建表 + 演示账号 + Chroma 自动迁移 ----------
@app.on_event("startup")
async def startup():
    # 调大 asyncio.to_thread 默认线程池（密码哈希等同步阻塞操作在高并发下排队），16 线程
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=16))

    # SQLAlchemy 幂等建表（含 login_logs 审计表；已存在的表不动）
    await init_db()

    # 创建 demo 用户（无论环境变量是否启用，都强制创建）
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == "demo"))
        user = result.scalar_one_or_none()
        if not user:
            demo_user = User(
                username="demo",
                email="demo@test.com",
                hashed_password=hash_password("demo123"),
                is_active=True
            )
            session.add(demo_user)
            await session.commit()
            logger.info("演示账号已强制创建: demo / demo123")
        else:
            logger.info("演示账号已存在: demo / demo123")
            # 演示账号密码固定，每次启动按当前哈希成本重写，
            # 调整 PASSWORD_HASH_ROUNDS 后旧哈希（高 rounds）自动刷新，避免拖慢登录
            user.hashed_password = hash_password("demo123")
            await session.commit()
    await redis_client.connect()

    # Chroma 自检：旧 L2 集合自动重建为 cosine 并灌库（失败不阻塞启动，下次启动会重试）
    try:
        from rag.vector_store import rebuild_collection_if_needed
        result = await asyncio.to_thread(rebuild_collection_if_needed)
        loguru_logger.bind(**result).info("chroma_auto_rebuild")
    except Exception as e:
        loguru_logger.bind(error=str(e)).warning("chroma_auto_rebuild_failed")


# ---------- 中间件：loguru 链路耗时（JSON 结构化） ----------
@app.middleware("http")
async def log_request_time(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = (time.perf_counter() - start_time) * 1000
    loguru_logger.bind(
        method=request.method,
        path=request.url.path,
        ms=round(process_time, 2),
        status=response.status_code,
    ).info("request_time")
    response.headers["X-Process-Time"] = f"{process_time:.2f}ms"
    return response


# ---------- 路由 ----------
app.include_router(auth.router, prefix="/api/v1", tags=["认证"])
app.include_router(ai.router, prefix="/api/v1", tags=["AI"])

from app.api.v1.endpoints import agent, rag
app.include_router(agent.router, prefix="/api/v1", tags=["Agent"])
app.include_router(rag.router, prefix="/api/v1", tags=["RAG"])

@app.get("/")
async def root():
    return {"msg": "FastAPI 项目已启动", "docs": "/docs"}









