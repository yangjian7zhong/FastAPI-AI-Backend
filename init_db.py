import asyncio
from app.core.database import engine
from app.models.user import Base

async def init():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('数据库表创建成功')

if __name__ == '__main__':
    asyncio.run(init())