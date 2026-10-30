"""演示一键体验入口。

为什么要有这个模块：
之前把演示账号密码直接写在接口文档（`/docs` 的描述）里，任何人打开页面或拉
`/openapi.json` 都能拿到。改成由服务端签发一枚**短效 token**，页面里不再出现密码，
泄露面收敛成"2 小时有效、可随时吊销（改 demo 用户密码或停用账号即失效）"。

配套页面：`GET /demo`（见 main.py），打开即自动取 token，直接问答。
"""

from datetime import timedelta
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import create_access_token
from app.dao.user_dao import UserDAO

router = APIRouter()

# 体验 token 有效期：够面试官点开玩几分钟，过期就重新进页面
DEMO_TOKEN_TTL_HOURS = 2

# 简单的按 IP 限流：每 IP 每小时最多签发 N 次，防止被脚本刷着换 token
_ISSUE_WINDOW_SECONDS = 3600
_ISSUE_LIMIT_PER_IP = 20
_issue_log: dict[str, list[float]] = {}


def _rate_limited(ip: str) -> bool:
    """滑动窗口计数。单进程内存实现，够用；多 worker 下各算各的（可接受）。"""
    now = time.time()
    stamps = [t for t in _issue_log.get(ip, []) if now - t < _ISSUE_WINDOW_SECONDS]
    if len(stamps) >= _ISSUE_LIMIT_PER_IP:
        _issue_log[ip] = stamps
        return True
    stamps.append(now)
    _issue_log[ip] = stamps
    return False


@router.post("/demo/token")
async def issue_demo_token(request: Request, db: AsyncSession = Depends(get_db)):
    """为演示账号签发短效 access_token（不需要密码）。"""
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="体验入口请求过于频繁，请稍后再试")

    user = await UserDAO.get_by_username(db, "demo")
    if not user or not user.is_active:
        raise HTTPException(status_code=503, detail="演示账号暂不可用")

    token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(hours=DEMO_TOKEN_TTL_HOURS),
        token_version=user.token_version,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": DEMO_TOKEN_TTL_HOURS * 3600,
    }
