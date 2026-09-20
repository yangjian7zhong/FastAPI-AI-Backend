from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, Integer

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 吊销计数器：JWT 里带 ver 声明，校验时与这里的值比对。
    # 递增即让该用户所有已签发的 token 立即失效（改密 / 全端登出用）。
    # 之前服务端唯一的吊销手段是 Redis 黑名单，Redis 一挂就没有任何吊销能力；
    # 有了这个字段，"故障降级"才真的能靠数据库兜住。
    token_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default="1"
    )

    # 一对多：登录审计日志（selectinload 预加载，避免 N+1）
    login_logs: Mapped[list["LoginLog"]] = relationship(back_populates="user")
