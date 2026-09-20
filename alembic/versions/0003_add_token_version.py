"""add users.token_version

吊销计数器：JWT 里带 ver 声明，校验时与 users.token_version 比对。
递增即让该用户所有已签发的 token 立即失效。
新增这一列的动机是让服务端在 Redis 不可用时仍然具备吊销能力。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default='1' 保证存量行也能拿到初始值（非空列必须有默认值才能加成功）
    op.add_column(
        'users',
        sa.Column('token_version', sa.Integer(), nullable=False, server_default='1'),
    )


def downgrade() -> None:
    op.drop_column('users', 'token_version')
