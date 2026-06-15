"""add experiment sessions

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""

from alembic import op
import sqlalchemy as sa

revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'experiment_session',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('group_id', sa.Text(), nullable=False),
        sa.Column('topic_id', sa.Text(), nullable=False),
        sa.Column('topic_title', sa.Text(), nullable=False),
        sa.Column('topic_question', sa.Text(), nullable=False),
        sa.Column('essay_id', sa.Text(), nullable=True),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('pre_survey', sa.JSON(), nullable=True),
        sa.Column('post_survey', sa.JSON(), nullable=True),
        sa.Column('consented_at', sa.BigInteger(), nullable=True),
        sa.Column('topic_shown_at', sa.BigInteger(), nullable=True),
        sa.Column('writing_started_at', sa.BigInteger(), nullable=True),
        sa.Column('essay_submitted_at', sa.BigInteger(), nullable=True),
        sa.Column('post_survey_submitted_at', sa.BigInteger(), nullable=True),
        sa.Column('completed_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('user_id', 'group_id', name='uq_experiment_session_user_group'),
    )
    op.create_index('ix_experiment_session_user_state', 'experiment_session', ['user_id', 'state'])


def downgrade():
    op.drop_index('ix_experiment_session_user_state', table_name='experiment_session')
    op.drop_table('experiment_session')

