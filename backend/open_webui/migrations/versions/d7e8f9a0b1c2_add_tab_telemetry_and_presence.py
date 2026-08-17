"""add tab telemetry schema version, extension presence, and repair indexes

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
"""

from alembic import op
import sqlalchemy as sa


revision = 'd7e8f9a0b1c2'
down_revision = 'c6d7e8f9a0b1'
branch_labels = None
depends_on = None


def _index_names(inspector, table):
    return {row['name'] for row in inspector.get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column['name'] for column in inspector.get_columns('experiment_telemetry_event')}
    if 'schema_version' not in columns:
        with op.batch_alter_table('experiment_telemetry_event') as batch_op:
            batch_op.add_column(sa.Column('schema_version', sa.Integer(), nullable=False, server_default='1'))

    inspector = sa.inspect(bind)
    indexes = _index_names(inspector, 'experiment_telemetry_event')
    required = {
        'ix_experiment_telemetry_event_task_question': ['session_task_id', 'question_id'],
        'ix_experiment_telemetry_event_request': ['request_id'],
        'ix_experiment_telemetry_event_condition_plan': ['condition_id', 'plan_id'],
        'ix_experiment_telemetry_event_session_type_time': [
            'experiment_session_id',
            'event_type',
            'event_time',
        ],
    }
    for name, fields in required.items():
        if name not in indexes:
            op.create_index(name, 'experiment_telemetry_event', fields)

    if not inspector.has_table('experiment_telemetry_extension_presence'):
        op.create_table(
            'experiment_telemetry_extension_presence',
            sa.Column('experiment_session_id', sa.Text(), primary_key=True),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('extension_version', sa.Text(), nullable=False),
            sa.Column('schema_version', sa.Integer(), nullable=False),
            sa.Column('tabs_permission', sa.Boolean(), nullable=False),
            sa.Column('incognito_allowed', sa.Boolean(), nullable=False),
            sa.Column('origin', sa.Text(), nullable=False),
            sa.Column('connected_at', sa.BigInteger(), nullable=False),
            sa.Column('last_seen_at', sa.BigInteger(), nullable=False),
        )
        op.create_index(
            'ix_experiment_telemetry_presence_user_seen',
            'experiment_telemetry_extension_presence',
            ['user_id', 'last_seen_at'],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('experiment_telemetry_extension_presence'):
        op.drop_index(
            'ix_experiment_telemetry_presence_user_seen',
            table_name='experiment_telemetry_extension_presence',
        )
        op.drop_table('experiment_telemetry_extension_presence')
    indexes = _index_names(sa.inspect(bind), 'experiment_telemetry_event')
    if 'ix_experiment_telemetry_event_session_type_time' in indexes:
        op.drop_index(
            'ix_experiment_telemetry_event_session_type_time',
            table_name='experiment_telemetry_event',
        )
    columns = {column['name'] for column in sa.inspect(bind).get_columns('experiment_telemetry_event')}
    if 'schema_version' in columns:
        with op.batch_alter_table('experiment_telemetry_event') as batch_op:
            batch_op.drop_column('schema_version')
