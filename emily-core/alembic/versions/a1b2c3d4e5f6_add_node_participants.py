"""add_node_participants

Revision ID: a1b2c3d4e5f6
Revises: 4360c4ea5ae3
Create Date: 2026-07-25 10:00:00.000000

节点参与人关联表 —— 支持多对多协同，每个节点可有多个参与人。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '4360c4ea5ae3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add node_participants table."""
    op.create_table('node_participants',
        sa.Column('id', sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column('node_id', sa.VARCHAR(length=100), autoincrement=False, nullable=False,
                  comment='节点ID（FK→project_nodes.node_id）'),
        sa.Column('user_id', sa.VARCHAR(length=100), autoincrement=False, nullable=False,
                  comment='参与人ID（FK→users.id）'),
        sa.Column('participant_role', sa.VARCHAR(length=20), autoincrement=False, nullable=False,
                  server_default='participant', comment='参与角色：participant / approver / observer'),
        sa.Column('added_by', sa.VARCHAR(length=100), autoincrement=False, nullable=False,
                  server_default='', comment='添加人ID'),
        sa.Column('added_at', sa.VARCHAR(length=50), autoincrement=False, nullable=False,
                  comment='添加时间（ISO8601）'),
        sa.PrimaryKeyConstraint('id', name=op.f('node_participants_pkey')),
        sa.UniqueConstraint('node_id', 'user_id', name='uq_np_node_user'),
    )
    op.create_index(op.f('idx_np_node'), 'node_participants', ['node_id'], unique=False)
    op.create_index(op.f('idx_np_user'), 'node_participants', ['user_id'], unique=False)


def downgrade() -> None:
    """Remove node_participants table."""
    op.drop_index(op.f('idx_np_user'), table_name='node_participants')
    op.drop_index(op.f('idx_np_node'), table_name='node_participants')
    op.drop_table('node_participants')
