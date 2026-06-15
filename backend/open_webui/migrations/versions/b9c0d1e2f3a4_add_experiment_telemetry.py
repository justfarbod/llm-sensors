"""add experiment telemetry

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
"""

from alembic import op
import sqlalchemy as sa

revision = 'b9c0d1e2f3a4'
down_revision = 'a8b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'experiment_telemetry_event',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('experiment_session_id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('event_time', sa.BigInteger(), nullable=False),
        sa.Column('field_context', sa.Text(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_experiment_telemetry_event_session_time',
        'experiment_telemetry_event',
        ['experiment_session_id', 'event_time'],
    )
    op.create_index(
        'ix_experiment_telemetry_event_user_session',
        'experiment_telemetry_event',
        ['user_id', 'experiment_session_id'],
    )
    op.create_table(
        'experiment_telemetry_summary',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('experiment_session_id', sa.Text(), nullable=False),
        sa.Column('total_keystrokes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_inter_key_interval_ms', sa.Float(), nullable=True),
        sa.Column('avg_key_hold_duration_ms', sa.Float(), nullable=True),
        sa.Column('pause_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('longest_pause_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('copy_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cut_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('paste_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_pasted_chars', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tab_switch_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_time_away_ms', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('inter_key_interval_total_ms', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('inter_key_interval_sample_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('key_hold_duration_total_ms', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('key_hold_duration_sample_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('experiment_session_id', name='uq_experiment_telemetry_summary_session'),
    )
    op.create_index(
        'ix_experiment_telemetry_summary_user_session',
        'experiment_telemetry_summary',
        ['user_id', 'experiment_session_id'],
    )


def downgrade():
    op.drop_index('ix_experiment_telemetry_summary_user_session', table_name='experiment_telemetry_summary')
    op.drop_table('experiment_telemetry_summary')
    op.drop_index('ix_experiment_telemetry_event_user_session', table_name='experiment_telemetry_event')
    op.drop_index('ix_experiment_telemetry_event_session_time', table_name='experiment_telemetry_event')
    op.drop_table('experiment_telemetry_event')
