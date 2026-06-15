"""add experiment dashboard fields

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""

from alembic import op
import sqlalchemy as sa

revision = 'a8b9c0d1e2f3'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('experiment_session') as batch_op:
        batch_op.add_column(sa.Column('pre_survey_submitted_at', sa.BigInteger(), nullable=True))

    with op.batch_alter_table('essay') as batch_op:
        batch_op.add_column(sa.Column('word_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('character_count', sa.Integer(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text('SELECT id, content FROM essay')).fetchall()
    for essay_id, content in rows:
        content = content or ''
        connection.execute(
            sa.text(
                'UPDATE essay SET word_count = :word_count, character_count = :character_count '
                'WHERE id = :essay_id'
            ),
            {
                'essay_id': essay_id,
                'word_count': len(content.split()),
                'character_count': len(content),
            },
        )

    op.create_index('ix_experiment_session_group_created', 'experiment_session', ['group_id', 'created_at'])
    op.create_index('ix_experiment_session_topic_created', 'experiment_session', ['topic_id', 'created_at'])
    op.create_index('ix_experiment_session_state_created', 'experiment_session', ['state', 'created_at'])


def downgrade():
    op.drop_index('ix_experiment_session_state_created', table_name='experiment_session')
    op.drop_index('ix_experiment_session_topic_created', table_name='experiment_session')
    op.drop_index('ix_experiment_session_group_created', table_name='experiment_session')

    with op.batch_alter_table('essay') as batch_op:
        batch_op.drop_column('character_count')
        batch_op.drop_column('word_count')

    with op.batch_alter_table('experiment_session') as batch_op:
        batch_op.drop_column('pre_survey_submitted_at')
