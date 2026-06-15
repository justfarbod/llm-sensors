"""add essay table

Revision ID: f2a3b4c5d6e7
Revises: a0b1c2d3e4f5
Create Date: 2026-06-15 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = 'f2a3b4c5d6e7'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'essay',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_essay_user_created_at', 'essay', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_essay_user_created_at', table_name='essay')
    op.drop_table('essay')
