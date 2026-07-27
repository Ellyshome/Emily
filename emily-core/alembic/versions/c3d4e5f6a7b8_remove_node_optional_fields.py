"""remove_node_optional_fields

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-27 13:00:00.000000

删除 project_nodes 表中 7 个字段（选填业务字段除 remark 外全部删除 + progress + sort_order）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 删除索引 ──
    op.drop_index('idx_nodes_stage', table_name='project_nodes', if_exists=True)
    op.drop_index('idx_nodes_parent', table_name='project_nodes', if_exists=True)

    # ── 删除列 ──
    with op.batch_alter_table('project_nodes') as batch_op:
        batch_op.drop_column('land_parcel_id')
        batch_op.drop_column('parent_node_id')
        batch_op.drop_column('stage_id')
        batch_op.drop_column('child_weight')
        batch_op.drop_column('startup_doc_id')
        batch_op.drop_column('progress')
        batch_op.drop_column('sort_order')


def downgrade() -> None:
    with op.batch_alter_table('project_nodes') as batch_op:
        batch_op.add_column(sa.Column('land_parcel_id', sa.String(100), server_default=sa.text("''"), comment='关联地块ID'))
        batch_op.add_column(sa.Column('parent_node_id', sa.String(100), server_default=sa.text("''"), comment='父节点ID'))
        batch_op.add_column(sa.Column('stage_id', sa.Integer(), server_default=sa.text('0'), comment='所属阶段ID'))
        batch_op.add_column(sa.Column('child_weight', sa.String(), server_default=sa.text("'1.0000'"), comment='子节点权重'))
        batch_op.add_column(sa.Column('startup_doc_id', sa.String(100), server_default=sa.text("''"), comment='启动文档记录ID'))
        batch_op.add_column(sa.Column('progress', sa.String(), server_default=sa.text("'0.00'"), comment='整体进度'))
        batch_op.add_column(sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), comment='排序序号'))

    op.create_index('idx_nodes_stage', 'project_nodes', ['stage_id'])
    op.create_index('idx_nodes_parent', 'project_nodes', ['parent_node_id'])
