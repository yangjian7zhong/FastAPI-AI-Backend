from fastapi import APIRouter, Depends, BackgroundTasks, Security, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timedelta
import os
import asyncio
import time
import logging

from app.core.database import get_db
from app.core.security import decode_token, create_access_token, create_refresh_token, verify_password
from app.core.config import settings
from app.schemas.user import UserRegister, UserResponse, UserLogin, RefreshRequest
from app.services.user import UserService
from app.models.user import User
from app.models.login_log import LoginLog
from app.dao.user_dao import UserDAO
from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)

api_key_scheme = APIKeyHeader(name='Authorization', auto_error=False)

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='login')


@router.post('/register')
async def register(
        user_data: UserRegister,
        background_tasks: BackgroundTasks,
        db: AsyncSession = Depends(get_db)
):
    user = await UserService.register(user_data, db, background_tasks)

    activation_token = create_access_token(
        data={"sub": str(user.id), "type": "activation"},
        expires_delta=timedelta(minutes=settings.ACTIVATION_TOKEN_EXPIRE_MINUTES)
    )
    activation_link = f"{settings.BASE_URL}/api/v1/activate?token={activation_token}"

    print(f"注册成功，激活链接: {activation_link}")

    return {
        'msg': '注册成功！请点击下方链接激活账号（10分钟内有效）',
        'activation_link': activation_link,
        'user_id': user.id
    }


@router.get('/activate')
async def activate_account(
        token: str,
        db: AsyncSession = Depends(get_db)
):
    print(f"进入激活接口，token: {token[:20]}...")  # 打印前20字符防止刷屏

    payload = decode_token(token)
    print(f"解析后的 payload: {payload}")

    if payload.get("type") != "activation":
        raise HTTPException(status_code=400, detail="无效的激活链接")

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=400, detail="无效的激活链接")

    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的激活链接")

    print(f"查询 user_id: {user_id}")

    user = await UserDAO.get_by_id(db, user_id)
    if not user:
        print(f"用户不存在: id={user_id}")
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.is_active:
        return {'msg': '账号已激活'}

    user.is_active = True
    await db.commit()
    print(f"用户激活成功: {user.username} (id={user.id})")
    return {'msg': '账号激活成功！'}


@router.post('/login')
async def login(
        login_data: UserLogin,
        request: Request,
        db: AsyncSession = Depends(get_db)
):
    user = await UserDAO.get_by_username(db, login_data.username)
    if not user or not await asyncio.to_thread(
        verify_password, login_data.password, user.hashed_password
    ):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="请先激活账号")

    # 登录审计：记录 IP 与时间（登录日志表，供 /users/me 展示最近登录）。
    # 写入失败不影响登录主流程（如 SQLite 并发写锁），仅告警降级
    try:
        ip = request.client.host if request.client else None
        db.add(LoginLog(user_id=user.id, ip=ip))
        await db.commit()
    except Exception as e:
        logger.warning("登录审计写入失败（已降级，不影响登录）: %s", e)
        try:
            await db.rollback()
        except Exception:
            pass

    # JWT 双 Token：access（短效，业务请求）+ refresh（长效，仅换新 access）
    access_token = create_access_token(data={"sub": str(user.id)})
    refresh_token = create_refresh_token(data={"sub": str(user.id)})
    return {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'bearer'
    }


@router.post('/refresh')
async def refresh_access_token(
        req: RefreshRequest,
        db: AsyncSession = Depends(get_db)
):
    """用 refresh token 换取新的 access token（access 过期后无需重新登录）"""
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="无效的刷新令牌")

    # 黑名单校验：refresh token 已登出则拒绝
    if await redis_client.exists(f"token_blacklist:{req.refresh_token}"):
        raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="无效的刷新令牌")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="无效的刷新令牌")

    user = await UserDAO.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号未激活")

    access_token = create_access_token(data={"sub": str(user.id)})
    return {'access_token': access_token, 'token_type': 'bearer'}


@router.post('/logout')
async def logout(
        token: str = Security(api_key_scheme),
):
    """登出：把当前 access token 加入 Redis 黑名单（有效期=token 剩余时长）"""
    raw = token
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if raw.startswith("Bearer "):
        raw = raw[7:]
    payload = decode_token(raw)
    if payload.get("type") != "access":
        raise HTTPException(status_code=400, detail="仅支持登出 access token")

    exp = payload.get("exp")
    if exp:
        ttl = max(1, int(exp - time.time()))
    else:
        ttl = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    await redis_client.setex(f"token_blacklist:{raw}", ttl, "1")
    return {'msg': '已退出登录'}


async def get_current_user(
        token: str = Security(api_key_scheme),
        db: AsyncSession = Depends(get_db)
):
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if token.startswith("Bearer "):
        token = token[7:]
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        user_id = int(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Redis 黑名单校验（Upstash Redis）；Redis 故障时 exists 返回 False，
    # 自动降级为下面的 DB 验证兜底（故障降级DB验证）
    if await redis_client.exists(f"token_blacklist:{token}"):
        raise HTTPException(status_code=401, detail="Token 已失效，请重新登录")

    user = await UserDAO.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


@router.get("/users/me")
async def get_me(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    # selectinload 预加载登录历史（避免 N+1），返回最近一次登录时间；
    # 审计表不可用时降级为普通响应，不影响主流程
    resp = UserResponse.model_validate(current_user)
    try:
        user = await UserDAO.get_with_login_history(db, current_user.id)
        if user and user.login_logs:
            resp.last_login_at = max(log.created_at for log in user.login_logs)
    except Exception as e:
        logger.warning("登录历史读取失败（已降级）: %s", e)
    return resp
