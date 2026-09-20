from fastapi import APIRouter, Depends, BackgroundTasks, Security, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timedelta
import asyncio
import logging

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_activation_token,
    create_refresh_token,
    decode_token,
    token_remaining_seconds,
    verify_password,
)
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


# ==================== 内部辅助 ====================

def _strip_bearer(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw[7:] if raw.startswith("Bearer ") else raw


def _token_version_ok(payload: dict, user: User) -> bool:
    """DB 侧版本校验：token 里的 ver 必须等于用户当前的 token_version。

    这是唯一不受 Redis 影响的吊销手段：Redis 挂了，per-token 黑名单失效，
    但「全端登出 / 改密后作废」这类整批吊销仍然拦得住 —— 也就是"故障降级 DB 验证"
    的实际落点（原实现里数据库根本没有吊销字段，所以那句话是空的）。

    严格模式：不带 ver 声明的旧 token 一律判失效，部署后需要重新登录一次。
    """
    return payload.get("ver") == user.token_version


# ==================== 注册 / 激活 ====================

@router.post('/register')
async def register(
        user_data: UserRegister,
        background_tasks: BackgroundTasks,
        db: AsyncSession = Depends(get_db)
):
    user = await UserService.register(user_data, db, background_tasks)

    # 用专用签发函数（type=activation）。原先借 create_access_token 传 type，
    # 而那个函数内部会把 type 硬写成 "access"，于是 /activate 必然判"无效的激活链接"。
    activation_token = create_activation_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.ACTIVATION_TOKEN_EXPIRE_MINUTES)
    )
    activation_link = f"{settings.BASE_URL}/api/v1/activate?token={activation_token}"

    logger.info("注册成功 user_id=%s，已生成激活链接", user.id)

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
        return {'msg': '账号已激活'}

    user.is_active = True
    await db.commit()
    logger.info("用户激活成功: %s (id=%s)", user.username, user.id)
    return {'msg': '账号激活成功！'}


# ==================== 登录 ====================

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

    # JWT 双 Token：access（短效，业务请求）+ refresh（长效，仅换新 token 对）
    # 两个 token 都带 ver 声明，服务端可凭 users.token_version 整批吊销
    access_token = create_access_token(
        data={"sub": str(user.id)}, token_version=user.token_version
    )
    refresh_token = create_refresh_token(
        data={"sub": str(user.id)}, token_version=user.token_version
    )
    return {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'bearer'
    }


# ==================== 刷新（带轮换） ====================

@router.post('/refresh')
async def refresh_access_token(
        req: RefreshRequest,
        db: AsyncSession = Depends(get_db)
):
    """用 refresh token 换取新的 token 对，并作废用过的这个 refresh token。

    轮换是这次补上的：原实现只签发新 access、不签发新 refresh，同一个 refresh token
    在 7 天内可以无限次重放 —— 泄露一次就等于长期有效。

    ⚠ 客户端契约变化：必须用响应里的新 refresh_token 覆盖旧的，否则下一次刷新会 401。
    """
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="无效的刷新令牌")

    # Redis 黑名单：这个 refresh token 是否已被作废（轮换/登出都会写这里）
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

    # DB 侧版本校验（不依赖 Redis）
    if not _token_version_ok(payload, user):
        raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")

    new_access = create_access_token(
        data={"sub": str(user.id)}, token_version=user.token_version
    )
    new_refresh = create_refresh_token(
        data={"sub": str(user.id)}, token_version=user.token_version
    )

    # 轮换：把刚用过的 refresh token 拉黑，TTL 取它自己的剩余寿命
    await redis_client.setex(
        f"token_blacklist:{req.refresh_token}",
        token_remaining_seconds(payload, settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60),
        "1",
    )

    return {
        'access_token': new_access,
        'refresh_token': new_refresh,
        'token_type': 'bearer'
    }


# ==================== 登出 ====================

@router.post('/logout')
async def logout(
        token: str = Security(api_key_scheme),
):
    """登出：把当前 token 加入 Redis 黑名单（TTL = 该 token 剩余有效期）。

    同时接受 access 和 refresh —— 原实现只允许 access，
    而 /refresh 里又写着"refresh token 已登出则拒绝"的检查，
    结果那个检查永远不可能命中（refresh 根本没途径进黑名单）。
    """
    raw = _strip_bearer(token)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(raw)
    token_type = payload.get("type")
    if token_type not in ("access", "refresh"):
        raise HTTPException(status_code=400, detail="仅支持登出 access / refresh token")

    default_ttl = (
        settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        if token_type == "access"
        else settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    )
    await redis_client.setex(
        f"token_blacklist:{raw}", token_remaining_seconds(payload, default_ttl), "1"
    )
    logger.info("登出成功 type=%s", token_type)
    return {'msg': '已退出登录'}


# ==================== 当前用户依赖 ====================

async def get_current_user(
        token: str = Security(api_key_scheme),
        db: AsyncSession = Depends(get_db)
):
    raw = _strip_bearer(token)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(raw)

    # 类型校验：原实现没查 type，于是 7 天有效期的 refresh token 可以直接当
    # access token 打所有业务接口，双 Token 的"access 短效"设计等于没生效。
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        user_id = int(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # ① Redis 黑名单：精确到单个 token。Redis 故障时 exists 返回 False（fail-open），
    #    这一层自动失效，由下面的数据库校验兜住"整批吊销"的场景。
    if await redis_client.exists(f"token_blacklist:{raw}"):
        raise HTTPException(status_code=401, detail="Token 已失效，请重新登录")

    user = await UserDAO.get_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # ② DB 版本校验：不依赖 Redis，Redis 挂了依然生效
    if not _token_version_ok(payload, user):
        raise HTTPException(status_code=401, detail="Token 已失效，请重新登录")

    return user


@router.post('/logout-all')
async def logout_all(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    """全端登出：递增 users.token_version，让该用户所有已签发的 token 立即失效。

    这条路径**只依赖数据库**：Redis 挂掉时 per-token 黑名单失效，
    但版本号层面的吊销照样拦得住 —— 这也是"故障降级 DB 验证"真正的落点。
    """
    current_user.token_version += 1
    await db.commit()
    logger.info("全端登出 user_id=%s token_version=%s",
                current_user.id, current_user.token_version)
    return {
        "msg": "已退出所有设备，请重新登录",
        "token_version": current_user.token_version,
    }


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
