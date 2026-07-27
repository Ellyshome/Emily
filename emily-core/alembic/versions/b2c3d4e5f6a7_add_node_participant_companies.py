"""add_node_participant_companies

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-27 10:00:00.000000

节点参与单位关联表 —— 支持节点与参建单位的多对多关联。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add node_participant_companies table."""
    op.create_table('node_participant_companies',
        sa.Column('id', sa.VARCHAR(), autoincrement=False, nullable=False),
        sa.Column('node_id', sa.VARCHAR(length=100), autoincrement=False, nullable=False,
                  comment='节点ID（FK→project_nodes.node_id）'),
        sa.Column('company_id', sa.VARCHAR(length=100), autoincrement=False, nullable=False,
                  comment='参与单位ID（FK→company_info.id）'),
        sa.Column('added_by', sa.VARCHAR(length=100), autoincrement=False, nullable=False,
                  server_default='', comment='添加人ID'),
        sa.Column('added_at', sa.VARCHAR(length=50), autoincrement=False, nullable=False,
                  comment='添加时间（ISO8601）'),
        sa.PrimaryKeyConstraint('id', name=op.f('node_participant_companies_pkey')),
        sa.UniqueConstraint('node_id', 'company_id', name='uq_npc_node_company'),
    )
    op.create_index(op.f('idx_npc_node'), 'node_participant_companies', ['node_id'], unique=False)
    op.create_index(op.f('idx_npc_company'), 'node_participant_companies', ['company_id'], unique=False)


def downgrade() -> None:
    """Remove node_participant_companies table."""
    op.drop_index(op.f('idx_npc_company'), table_name='node_participant_companies')
    op.drop_index(op.f('idx_npc_node'), table_name='node_participant_companies')
    op.drop_table('node_participant_companies')
