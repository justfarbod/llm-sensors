"""add essay topics

Revision ID: e6f7a8b9c0d1
Revises: f2a3b4c5d6e7
Create Date: 2026-06-15 00:00:01.000000

"""

from alembic import op
import sqlalchemy as sa

revision = 'e6f7a8b9c0d1'
down_revision = 'f2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('essay') as batch_op:
        batch_op.add_column(sa.Column('topic_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('topic_title', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('topic_question', sa.Text(), nullable=True))

    op.create_table(
        'essay_topic',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'essay_topic_assignment',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('group_id', sa.Text(), nullable=False),
        sa.Column('topic_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'group_id', name='uq_essay_topic_assignment_user_group'),
    )
    op.create_index(
        'ix_essay_topic_assignment_topic',
        'essay_topic_assignment',
        ['topic_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_essay_topic_assignment_topic', table_name='essay_topic_assignment')
    op.drop_table('essay_topic_assignment')
    op.drop_table('essay_topic')

    with op.batch_alter_table('essay') as batch_op:
        batch_op.drop_column('topic_question')
        batch_op.drop_column('topic_title')
        batch_op.drop_column('topic_id')
