"""add portable experiment workflows

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""

from alembic import op
import sqlalchemy as sa


revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'experiment_workflow',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('definition', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_by', sa.Text(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_by', sa.Text(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_experiment_workflow_updated_at', 'experiment_workflow', ['updated_at'])

    with op.batch_alter_table('essay_topic') as batch_op:
        batch_op.add_column(sa.Column('locked_at', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('workflow_managed', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('question_task') as batch_op:
        batch_op.add_column(sa.Column('workflow_managed', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('survey_task') as batch_op:
        batch_op.add_column(sa.Column('workflow_managed', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('survey_task') as batch_op:
        batch_op.drop_column('workflow_managed')
    with op.batch_alter_table('question_task') as batch_op:
        batch_op.drop_column('workflow_managed')
    with op.batch_alter_table('essay_topic') as batch_op:
        batch_op.drop_column('workflow_managed')
        batch_op.drop_column('locked_at')
    op.drop_index('ix_experiment_workflow_updated_at', table_name='experiment_workflow')
    op.drop_table('experiment_workflow')
