# 集中导入全部模型，确保 Base.metadata 感知所有表（create_all / Alembic 用）
from app.models.user import Base, User
from app.models.login_log import LoginLog
