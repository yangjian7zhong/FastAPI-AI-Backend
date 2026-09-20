import time
import uuid
from datetime import datetime, timedelta

from jose import jwt as _jwt   # 注意这里用了别名 _jwt
from fastapi import HTTPException
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(
    schemes=["sha256_crypt"],
    deprecated="auto",
    # rounds 由配置控制：验证旧哈希时 passlib 自动按哈希内 rounds 校验，不影响存量用户
    sha256_crypt__rounds=settings.PASSWORD_HASH_ROUNDS,
)


# ==================== 密码 ====================

def hash_password(plain: str) -> str:
    if isinstance(plain, bytes):
        plain = plain.decode('utf-8')
    import re
    plain = re.sub(r'[\x00-\x1f\x7f]', '', plain)
    plain = plain.strip()
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    if isinstance(plain, bytes):
        plain = plain.decode('utf-8')
    import re
    plain = re.sub(r'[\x00-\x1f\x7f]', '', plain)
    plain = plain.strip()
    return pwd_context.verify(plain, hashed)


# ==================== JWT ====================

def _encode(data: dict, token_type: str, expire: datetime,
            token_version: int | None = None) -> str:
    """统一签发入口。

    token_type 只能由这里写入，不从 data 里取——原实现是
    `to_encode.update({"type": "access"})`，会把调用方传进来的
    `{"type": "activation"}` 直接覆盖成 access，导致 /activate 里
    `payload.get("type") != "activation"` 永远成立、
    注册后的激活链路整条走不通（已实测确认）。

    token_version 为 None 时不下发 ver 声明（仅激活 token 用）。
    """
    to_encode = {k: v for k, v in data.items() if k != "type"}
    to_encode["type"] = token_type
    to_encode["exp"] = expire
    # jti（唯一 ID）：同一秒内签发的两个 token 若声明完全相同，JWT 字符串会字节一致。
    # refresh 轮换时会先把旧 token 拉黑，如果新 token 与它相同，等于把新 token 也拉黑了。
    to_encode["jti"] = uuid.uuid4().hex
    if token_version is not None:
        to_encode["ver"] = token_version
    return _jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(data: dict, expires_delta: timedelta = None,
                        token_version: int | None = None) -> str:
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return _encode(data, "access", expire, token_version)


def create_refresh_token(data: dict, expires_delta: timedelta = None,
                         token_version: int | None = None) -> str:
    """JWT 双 Token：refresh token 有效期更长，仅用于换取新的 token 对"""
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    )
    return _encode(data, "refresh", expire, token_version)


def create_activation_token(data: dict, expires_delta: timedelta = None) -> str:
    """激活链接专用 token（type=activation，不带 ver）。"""
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACTIVATION_TOKEN_EXPIRE_MINUTES)
    )
    return _encode(data, "activation", expire, None)


def decode_token(token: str):
    try:
        return _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except _jwt.JWTError:
        raise HTTPException(status_code=401, detail="无效或过期的Token")


def token_remaining_seconds(payload: dict, default_seconds: int) -> int:
    """算 token 剩余有效期，用作 Redis 黑名单 TTL（至少要 1 秒，否则 setex 会报错）。"""
    exp = payload.get("exp")
    if not exp:
        return max(1, default_seconds)
    return max(1, int(exp - time.time()))
