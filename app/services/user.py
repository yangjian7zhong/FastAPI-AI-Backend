import asyncio

# app/services/user.py
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, BackgroundTasks, Depends
from datetime import timedelta

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token, decode_token
from app.core.config import settings
from app.dao.user_dao import UserDAO
from app.schemas.user import UserRegister
from email_utils import send_activation_email
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


class UserService:

    @staticmethod
    async def register(user_data: UserRegister, db: AsyncSession, background_tasks: BackgroundTasks):
        # 如果 ENV=test，自动删除同名用户（防止重复注册报错）
        if settings.ENV == "test":
            old_user = await UserDAO.get_by_username(db, user_data.username)
            if old_user:
                await db.delete(old_user)
                await db.commit()
                print(f"已清理旧用户: {old_user.username}")

            old_user_by_email = await UserDAO.get_by_email(db, user_data.email)
            if old_user_by_email:
                await db.delete(old_user_by_email)
                await db.commit()
                print(f"已清理旧用户: {old_user_by_email.username}")

        # 检查用户名和邮箱是否已存在
        if await UserDAO.get_by_username(db, user_data.username):
            raise HTTPException(status_code=400, detail="用户名已存在")

        if await UserDAO.get_by_email(db, user_data.email):
            raise HTTPException(status_code=400, detail="邮箱已被注册")

        # 创建新用户
        new_user = await UserDAO.create(
            db,
            username=user_data.username.strip(),
            email=user_data.email,
            hashed_password=hash_password(user_data.password),
            is_active=False,
        )

        # 生成激活 Token 时使用 user.id
        token = create_access_token(
            data={"sub": str(new_user.id), "type": "activation"},
            expires_delta=timedelta(minutes=settings.ACTIVATION_TOKEN_EXPIRE_MINUTES)
        )
        background_tasks.add_task(send_activation_email, new_user.email, new_user.username, token)
        return new_user

    @staticmethod
    async def activate_account(token: str, db: AsyncSession):
        payload = decode_token(token)
        if payload.get("type") != "activation":
            raise HTTPException(status_code=400, detail="无效的激活链接")

        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=400, detail="无效的激活链接")

        try:
            user_id = int(user_id_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的激活链接")

        user = await UserDAO.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.is_active:
            return user

        user.is_active = True
        await db.commit()
        return user

    @staticmethod
    async def login(username: str, password: str, db: AsyncSession):
        user = await UserDAO.get_by_username(db, username)
        if not user:
            raise HTTPException(status_code=401, detail='账号或密码错误')
        if not await asyncio.to_thread(verify_password, password, user.hashed_password):
            raise HTTPException(status_code=401, detail='账号或密码错误')
        if not user.is_active:
            raise HTTPException(status_code=403, detail="请先激活账号")

        access_token = create_access_token(data={"sub": str(user.id)})
        return access_token

    @staticmethod
    async def get_current_user_by_token(token: str, db: AsyncSession):
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="无效的Token")
        try:
            user_id = int(user_id_str)
        except ValueError:
            raise HTTPException(status_code=401, detail="无效的Token")
        user = await UserDAO.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user


# FastAPI 依赖注入函数
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
):
    return await UserService.get_current_user_by_token(token, db)


















































