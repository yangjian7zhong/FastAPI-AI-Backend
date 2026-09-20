from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import inspect, text
from app.models.user import Base
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# 判断是否为 SQLite
is_sqlite = settings.DATABASE_URL.startswith("sqlite")

# 构造引擎参数
engine_kwargs = {
    "echo": False,
    "pool_pre_ping": settings.DB_POOL_PRE_PING,
    "pool_recycle": settings.DB_POOL_RECYCLE,
}

# 只有非 SQLite 才传递连接池大小参数
if not is_sqlite:
    engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# 幂等补列登记表：(表名, 列名, DDL 类型片段)
# 背景：create_all 只会补"缺失的表"，不会给已存在的表加列；而部署流程
# （Procfile / nixpacks）只跑 uvicorn、不执行 alembic upgrade。
# 所以新增列必须在这里登记一行，否则老库启动后依然缺列、查询直接报错。
# alembic 迁移文件同时保留（0003），供需要正式迁移的环境使用。
_IDEMPOTENT_COLUMNS = (
    ("users", "token_version", "INTEGER NOT NULL DEFAULT 1"),
)


async def _ensure_columns(conn) -> list:
    """幂等补列：列已存在则跳过。返回本次新增的列名列表。"""

    def _sync(sync_conn):
        inspector = inspect(sync_conn)
        added = []
        for table, column, ddl_type in _IDEMPOTENT_COLUMNS:
            try:
                existing = {c["name"] for c in inspector.get_columns(table)}
            except Exception:
                continue          # 表还不存在，交给 create_all
            if column not in existing:
                sync_conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
                )
                added.append(f"{table}.{column}")
        return added

    added = await conn.run_sync(_sync)
    if added:
        logger.info("幂等补列完成: %s", ", ".join(added))
    return added


async def init_db():
    """启动时自动建表 + 幂等补列"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_columns(conn)
        logger.info("数据库表创建完成（PostgreSQL/SQLite）")




