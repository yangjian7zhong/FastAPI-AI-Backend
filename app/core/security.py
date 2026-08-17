from passlib.context import CryptContext
from jose import jwt as _jwt   # 注意这里用了别名 _jwt
from datetime import datetime, timedelta
from fastapi import HTTPException, status
from app.core.config import settings

pwd_context = CryptContext(
    schemes=["sha256_crypt"],
    deprecated="auto",
    # rounds 由配置控制：验证旧哈希时 passlib 自动按哈希内 rounds 校验，不影响存量用户
    sha256_crypt__rounds=settings.PASSWORD_HASH_ROUNDS,
)

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

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return _jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict, expires_delta: timedelta = None):
    """JWT 双 Token：refresh token 有效期更长，仅用于换取新的 access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "refresh"})
    return _jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str):
    try:
        return _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except _jwt.JWTError:
        raise HTTPException(status_code=401, detail="无效或过期的Token")