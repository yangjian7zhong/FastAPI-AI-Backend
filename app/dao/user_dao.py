from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User


class UserDAO:
    """数据访问层（DAO）：统一封装 User 相关的数据库操作，
    上层 Service/Route 不直接拼 SQLAlchemy 查询。"""

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> User | None:
        return (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> User | None:
        return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
        return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        username: str,
        email: str,
        hashed_password: str,
        is_active: bool = False,
    ) -> User:
        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            is_active=is_active,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def get_with_login_history(db: AsyncSession, user_id: int) -> User | None:
        """selectinload 预加载登录历史，避免逐条查询登录记录的 N+1 问题"""
        return (await db.execute(
            select(User)
            .options(selectinload(User.login_logs))
            .where(User.id == user_id)
        )).scalar_one_or_none()
