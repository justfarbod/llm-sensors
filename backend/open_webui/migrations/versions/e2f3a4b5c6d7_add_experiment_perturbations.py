"""add experiment perturbations

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

import hmac
import hashlib
import uuid

from alembic import op
import sqlalchemy as sa

revision = 'e2f3a4b5c6d7'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'experiment_condition',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('plan_id', sa.Text(), sa.ForeignKey('experiment_plan.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('allocation_percent', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('is_control', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('plan_id', 'position', name='uq_experiment_condition_plan_position'),
    )
    op.create_index('ix_experiment_condition_plan_enabled', 'experiment_condition', ['plan_id', 'enabled'])
    op.create_table(
        'experiment_prompt_injection',
        sa.Column(
            'condition_id', sa.Text(), sa.ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('instruction', sa.Text(), nullable=False, server_default=''),
        sa.Column('position', sa.Text(), nullable=False, server_default='SYSTEM'),
        sa.Column('activation_mode', sa.Text(), nullable=False, server_default='EVERY_REQUEST'),
        sa.Column('activation_count', sa.Integer(), nullable=True),
        sa.Column('range_start', sa.Integer(), nullable=True),
        sa.Column('range_end', sa.Integer(), nullable=True),
        sa.Column('probability', sa.Float(), nullable=False, server_default='1'),
        sa.Column('scope', sa.Text(), nullable=False, server_default='ALL_TASKS'),
    )
    op.create_table(
        'experiment_memory_injection',
        sa.Column(
            'condition_id', sa.Text(), sa.ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('content', sa.Text(), nullable=False, server_default=''),
        sa.Column('persist_for_session', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('activation_mode', sa.Text(), nullable=False, server_default='EVERY_REQUEST'),
        sa.Column('activation_count', sa.Integer(), nullable=True),
        sa.Column('range_start', sa.Integer(), nullable=True),
        sa.Column('range_end', sa.Integer(), nullable=True),
        sa.Column('probability', sa.Float(), nullable=False, server_default='1'),
        sa.Column('scope', sa.Text(), nullable=False, server_default='ALL_TASKS'),
    )
    op.create_table(
        'experiment_warning_modal',
        sa.Column(
            'condition_id', sa.Text(), sa.ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('title', sa.Text(), nullable=False, server_default='Important reminder'),
        sa.Column(
            'message',
            sa.Text(),
            nullable=False,
            server_default='LLMs can make mistakes. Double-check important answers.',
        ),
        sa.Column('confirmation_text', sa.Text(), nullable=False, server_default='Continue'),
        sa.Column('must_acknowledge', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('cadence', sa.Text(), nullable=False, server_default='BEGINNING'),
        sa.Column('cadence_value', sa.Integer(), nullable=True),
        sa.Column('prompt_numbers', sa.JSON(), nullable=False, server_default='[]'),
    )
    op.create_table(
        'experiment_response_timing',
        sa.Column(
            'condition_id', sa.Text(), sa.ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('mode', sa.Text(), nullable=False, server_default='NORMAL'),
        sa.Column('delay_seconds', sa.Float(), nullable=True),
        sa.Column('show_loading', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('reveal_style', sa.Text(), nullable=False, server_default='FULL'),
        sa.Column('target_duration_seconds', sa.Float(), nullable=True),
        sa.Column('stream_unit', sa.Text(), nullable=False, server_default='CHARACTER'),
        sa.Column('minimum_chunk_size', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('maximum_chunk_size', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('punctuation_pauses', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('rate_value', sa.Float(), nullable=True),
        sa.Column('rate_unit', sa.Text(), nullable=False, server_default='CHARACTERS_PER_SECOND'),
    )
    op.create_table(
        'experiment_condition_task_scope',
        sa.Column(
            'condition_id', sa.Text(), sa.ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('perturbation_type', sa.Text(), primary_key=True),
        sa.Column(
            'plan_item_id', sa.Text(), sa.ForeignKey('experiment_plan_item.id', ondelete='CASCADE'), primary_key=True
        ),
    )
    op.create_table(
        'experiment_llm_request',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('experiment_session_id', sa.Text(), nullable=False),
        sa.Column('condition_id', sa.Text(), nullable=True),
        sa.Column('plan_id', sa.Text(), nullable=True),
        sa.Column('plan_version', sa.Integer(), nullable=True),
        sa.Column('session_task_id', sa.Text(), nullable=True),
        sa.Column('chat_id', sa.Text(), nullable=True),
        sa.Column('user_message_id', sa.Text(), nullable=True),
        sa.Column('assistant_message_id', sa.Text(), nullable=True),
        sa.Column('request_sequence', sa.Integer(), nullable=False),
        sa.Column('prompt_number', sa.Integer(), nullable=False),
        sa.Column('assignment_identifier', sa.Text(), nullable=True),
        sa.Column('prompt_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('memory_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('memory_latched', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('prompt_draw', sa.Float(), nullable=True),
        sa.Column('memory_draw', sa.Float(), nullable=True),
        sa.Column('prompt_randomization_id', sa.Text(), nullable=True),
        sa.Column('memory_randomization_id', sa.Text(), nullable=True),
        sa.Column('timing_mode', sa.Text(), nullable=False, server_default='NORMAL'),
        sa.Column('timing_parameters', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('request_at', sa.BigInteger(), nullable=False),
        sa.Column('provider_started_at', sa.BigInteger(), nullable=True),
        sa.Column('provider_first_token_at', sa.BigInteger(), nullable=True),
        sa.Column('provider_completed_at', sa.BigInteger(), nullable=True),
        sa.Column('artificial_delay_started_at', sa.BigInteger(), nullable=True),
        sa.Column('artificial_delay_ended_at', sa.BigInteger(), nullable=True),
        sa.Column('server_first_emit_at', sa.BigInteger(), nullable=True),
        sa.Column('server_completed_emit_at', sa.BigInteger(), nullable=True),
        sa.Column('client_first_visible_at', sa.BigInteger(), nullable=True),
        sa.Column('client_completed_visible_at', sa.BigInteger(), nullable=True),
        sa.Column('buffered', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('streaming_completed_normally', sa.Boolean(), nullable=True),
        sa.Column('navigated_away', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status', sa.Text(), nullable=False, server_default='PENDING'),
        sa.Column('error_type', sa.Text(), nullable=True),
        sa.Column('buffered_output', sa.JSON(), nullable=True),
        sa.Column('reveal_cursor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('experiment_session_id', 'request_sequence', name='uq_experiment_llm_request_sequence'),
    )
    op.create_index(
        'ix_experiment_llm_request_session_prompt', 'experiment_llm_request', ['experiment_session_id', 'prompt_number']
    )
    op.create_index('ix_experiment_llm_request_message', 'experiment_llm_request', ['assistant_message_id'])
    op.create_table(
        'experiment_warning_state',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('experiment_session_id', sa.Text(), nullable=False),
        sa.Column('condition_id', sa.Text(), nullable=False),
        sa.Column('plan_id', sa.Text(), nullable=False),
        sa.Column('active_elapsed_ms', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('last_trigger_key', sa.Text(), nullable=True),
        sa.Column('last_token', sa.Text(), nullable=True),
        sa.Column('pending_token', sa.Text(), nullable=True),
        sa.Column('pending_reason', sa.Text(), nullable=True),
        sa.Column('pending_trigger_key', sa.Text(), nullable=True),
        sa.Column('pending_prompt_count', sa.Integer(), nullable=True),
        sa.Column('pending_active_elapsed_ms', sa.BigInteger(), nullable=True),
        sa.Column('displayed_at', sa.BigInteger(), nullable=True),
        sa.Column('acknowledged_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('experiment_session_id', 'condition_id', 'plan_id', name='uq_experiment_warning_state'),
    )
    with op.batch_alter_table('experiment_session') as batch_op:
        batch_op.add_column(sa.Column('condition_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('condition_assignment_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('condition_assignment_draw', sa.Float(), nullable=True))
    with op.batch_alter_table('experiment_telemetry_event') as batch_op:
        batch_op.add_column(sa.Column('condition_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('plan_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('request_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('chat_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('message_id', sa.Text(), nullable=True))
    op.create_index('ix_experiment_session_condition', 'experiment_session', ['condition_id'])
    op.create_index('ix_experiment_telemetry_event_request', 'experiment_telemetry_event', ['request_id'])
    op.create_index(
        'ix_experiment_telemetry_event_condition_plan',
        'experiment_telemetry_event',
        ['condition_id', 'plan_id'],
    )

    connection = op.get_bind()
    plans = connection.execute(sa.text('SELECT id, created_at FROM experiment_plan')).fetchall()
    controls = {}
    for plan_id, created_at in plans:
        condition_id = str(uuid.uuid4())
        controls[plan_id] = condition_id
        connection.execute(
            sa.text(
                'INSERT INTO experiment_condition '
                '(id, plan_id, name, position, allocation_percent, enabled, is_control, created_at) '
                'VALUES (:id, :plan_id, :name, 0, 100, :enabled, :control, :created_at)'
            ),
            {
                'id': condition_id,
                'plan_id': plan_id,
                'name': 'Control',
                'enabled': True,
                'control': True,
                'created_at': created_at,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO experiment_prompt_injection (condition_id, enabled, instruction, position, activation_mode, probability, scope) VALUES (:id, :enabled, '', 'SYSTEM', 'EVERY_REQUEST', 1, 'ALL_TASKS')"
            ),
            {'id': condition_id, 'enabled': False},
        )
        connection.execute(
            sa.text(
                "INSERT INTO experiment_memory_injection (condition_id, enabled, content, persist_for_session, activation_mode, probability, scope) VALUES (:id, :enabled, '', :persist, 'EVERY_REQUEST', 1, 'ALL_TASKS')"
            ),
            {'id': condition_id, 'enabled': False, 'persist': False},
        )
        connection.execute(
            sa.text(
                "INSERT INTO experiment_warning_modal (condition_id, enabled, title, message, confirmation_text, must_acknowledge, cadence, prompt_numbers) VALUES (:id, :enabled, 'Important reminder', 'LLMs can make mistakes. Double-check important answers.', 'Continue', :must_ack, 'BEGINNING', :numbers)"
            ),
            {'id': condition_id, 'enabled': False, 'must_ack': True, 'numbers': '[]'},
        )
        connection.execute(
            sa.text(
                "INSERT INTO experiment_response_timing (condition_id, mode, show_loading, reveal_style, stream_unit, minimum_chunk_size, maximum_chunk_size, punctuation_pauses, rate_unit) VALUES (:id, 'NORMAL', :loading, 'FULL', 'CHARACTER', 1, 20, :pauses, 'CHARACTERS_PER_SECOND')"
            ),
            {'id': condition_id, 'loading': True, 'pauses': False},
        )
    sessions = connection.execute(
        sa.text('SELECT id, user_id, plan_id FROM experiment_session WHERE plan_id IS NOT NULL')
    ).fetchall()
    for session_id, user_id, plan_id in sessions:
        condition_id = controls.get(plan_id)
        if condition_id:
            from open_webui.env import WEBUI_SECRET_KEY

            digest = hmac.new(
                str(WEBUI_SECRET_KEY).encode(),
                f'condition:{plan_id}:{user_id}'.encode(),
                hashlib.sha256,
            ).hexdigest()
            draw = int(digest[:16], 16) / float(2**64)
            connection.execute(
                sa.text(
                    'UPDATE experiment_session SET condition_id=:condition_id, condition_assignment_id=:assignment_id, condition_assignment_draw=:draw WHERE id=:id'
                ),
                {'condition_id': condition_id, 'assignment_id': digest[:24], 'draw': draw, 'id': session_id},
            )


def downgrade():
    op.drop_index('ix_experiment_telemetry_event_condition_plan', table_name='experiment_telemetry_event')
    op.drop_index('ix_experiment_telemetry_event_request', table_name='experiment_telemetry_event')
    op.drop_index('ix_experiment_session_condition', table_name='experiment_session')
    with op.batch_alter_table('experiment_telemetry_event') as batch_op:
        for name in ('message_id', 'chat_id', 'request_id', 'plan_id', 'condition_id'):
            batch_op.drop_column(name)
    with op.batch_alter_table('experiment_session') as batch_op:
        for name in ('condition_assignment_draw', 'condition_assignment_id', 'condition_id'):
            batch_op.drop_column(name)
    op.drop_table('experiment_warning_state')
    op.drop_index('ix_experiment_llm_request_message', table_name='experiment_llm_request')
    op.drop_index('ix_experiment_llm_request_session_prompt', table_name='experiment_llm_request')
    op.drop_table('experiment_llm_request')
    op.drop_table('experiment_condition_task_scope')
    op.drop_table('experiment_response_timing')
    op.drop_table('experiment_warning_modal')
    op.drop_table('experiment_memory_injection')
    op.drop_table('experiment_prompt_injection')
    op.drop_index('ix_experiment_condition_plan_enabled', table_name='experiment_condition')
    op.drop_table('experiment_condition')
