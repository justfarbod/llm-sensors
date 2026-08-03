"""add ordered experiment plans and question tasks

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
"""

import json
import time
import uuid

from alembic import op
import sqlalchemy as sa

revision = 'c0d1e2f3a4b5'
down_revision = 'b9c0d1e2f3a4'
branch_labels = None
depends_on = None


def _uuid():
    return str(uuid.uuid4())


def _config(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return (value or {}).get('config', {}) if isinstance(value, dict) else {}


def upgrade():
    op.create_table(
        'question_task',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.Text(), nullable=False, server_default='DRAFT'),
        sa.Column('locked_at', sa.BigInteger(), nullable=True),
        sa.Column('archived_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_question_task_status_updated', 'question_task', ['status', 'updated_at'])

    op.create_table(
        'question_task_question',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('task_id', sa.Text(), sa.ForeignKey('question_task.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('question_type', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('max_score', sa.Numeric(10, 2), nullable=False),
        sa.Column('grading_mode', sa.Text(), nullable=False),
        sa.Column('image_file_id', sa.Text(), sa.ForeignKey('file.id', ondelete='SET NULL'), nullable=True),
        sa.Column('case_sensitive', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('task_id', 'position', name='uq_question_task_question_position'),
    )
    op.create_index('ix_question_task_question_task', 'question_task_question', ['task_id'])

    op.create_table(
        'question_choice',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'question_id', sa.Text(), sa.ForeignKey('question_task_question.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('is_correct', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint('question_id', 'position', name='uq_question_choice_position'),
    )
    op.create_index('ix_question_choice_question', 'question_choice', ['question_id'])

    op.create_table(
        'question_blank',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'question_id', sa.Text(), sa.ForeignKey('question_task_question.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column('blank_key', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.UniqueConstraint('question_id', 'blank_key', name='uq_question_blank_key'),
        sa.UniqueConstraint('question_id', 'position', name='uq_question_blank_position'),
    )
    op.create_index('ix_question_blank_question', 'question_blank', ['question_id'])

    op.create_table(
        'question_blank_accepted_answer',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('blank_id', sa.Text(), sa.ForeignKey('question_blank.id', ondelete='CASCADE'), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.UniqueConstraint('blank_id', 'position', name='uq_question_blank_answer_position'),
    )
    op.create_index('ix_question_blank_answer_blank', 'question_blank_accepted_answer', ['blank_id'])

    op.create_table(
        'question_free_text_config',
        sa.Column(
            'question_id', sa.Text(), sa.ForeignKey('question_task_question.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('expected_answer', sa.Text(), nullable=False),
        sa.Column('strictness', sa.Text(), nullable=False),
    )

    op.create_table(
        'experiment_plan',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('group_id', sa.Text(), sa.ForeignKey('group.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.Text(), nullable=False, server_default='DRAFT'),
        sa.Column('progression_mode', sa.Text(), nullable=False, server_default='STRICT_SEQUENTIAL'),
        sa.Column('chat_mode', sa.Text(), nullable=False, server_default='SHARED_EXPERIMENT'),
        sa.Column('survey_variant', sa.Text(), nullable=False, server_default='ESSAY'),
        sa.Column('locked_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('group_id', 'version', name='uq_experiment_plan_group_version'),
    )
    op.create_index('ix_experiment_plan_group_status', 'experiment_plan', ['group_id', 'status'])

    op.create_table(
        'experiment_plan_item',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('plan_id', sa.Text(), sa.ForeignKey('experiment_plan.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('task_type', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('question_task_id', sa.Text(), sa.ForeignKey('question_task.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('essay_topic_mode', sa.Text(), nullable=True),
        sa.Column('essay_topic_id', sa.Text(), sa.ForeignKey('essay_topic.id', ondelete='RESTRICT'), nullable=True),
        sa.UniqueConstraint('plan_id', 'position', name='uq_experiment_plan_item_position'),
    )
    op.create_index('ix_experiment_plan_item_plan', 'experiment_plan_item', ['plan_id'])

    op.create_table(
        'experiment_plan_item_topic',
        sa.Column(
            'plan_item_id', sa.Text(), sa.ForeignKey('experiment_plan_item.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('topic_id', sa.Text(), sa.ForeignKey('essay_topic.id', ondelete='RESTRICT'), primary_key=True),
    )

    op.create_table(
        'experiment_session_task',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'experiment_session_id',
            sa.Text(),
            sa.ForeignKey('experiment_session.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'plan_item_id', sa.Text(), sa.ForeignKey('experiment_plan_item.id', ondelete='RESTRICT'), nullable=True
        ),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('task_type', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('essay_topic_id', sa.Text(), nullable=True),
        sa.Column('essay_topic_title', sa.Text(), nullable=True),
        sa.Column('essay_topic_question', sa.Text(), nullable=True),
        sa.Column('question_task_id', sa.Text(), sa.ForeignKey('question_task.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('essay_id', sa.Text(), sa.ForeignKey('essay.id', ondelete='SET NULL'), nullable=True),
        sa.Column('essay_draft', sa.Text(), nullable=True),
        sa.Column('started_at', sa.BigInteger(), nullable=True),
        sa.Column('completed_at', sa.BigInteger(), nullable=True),
        sa.Column('finalized_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('experiment_session_id', 'position', name='uq_experiment_session_task_position'),
    )
    op.create_index(
        'ix_experiment_session_task_session_status', 'experiment_session_task', ['experiment_session_id', 'status']
    )

    op.create_table(
        'question_submission',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'session_task_id',
            sa.Text(),
            sa.ForeignKey('experiment_session_task.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='DRAFT'),
        sa.Column('grading_status', sa.Text(), nullable=False, server_default='NOT_STARTED'),
        sa.Column('current_score', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('maximum_score', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('submitted_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('session_task_id', name='uq_question_submission_session_task'),
    )
    op.create_index('ix_question_submission_user_status', 'question_submission', ['user_id', 'status'])

    op.create_table(
        'question_response',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'submission_id', sa.Text(), sa.ForeignKey('question_submission.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column(
            'question_id', sa.Text(), sa.ForeignKey('question_task_question.id', ondelete='RESTRICT'), nullable=False
        ),
        sa.Column('free_text_answer', sa.Text(), nullable=True),
        sa.Column('is_answered', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('grading_status', sa.Text(), nullable=False, server_default='NOT_STARTED'),
        sa.Column('generated_score', sa.Numeric(10, 2), nullable=True),
        sa.Column('effective_score', sa.Numeric(10, 2), nullable=True),
        sa.Column('grading_method', sa.Text(), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('submission_id', 'question_id', name='uq_question_response_submission_question'),
    )
    op.create_index('ix_question_response_submission_status', 'question_response', ['submission_id', 'grading_status'])

    op.create_table(
        'question_response_choice',
        sa.Column(
            'response_id', sa.Text(), sa.ForeignKey('question_response.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('choice_id', sa.Text(), sa.ForeignKey('question_choice.id', ondelete='RESTRICT'), primary_key=True),
    )
    op.create_table(
        'question_response_blank',
        sa.Column(
            'response_id', sa.Text(), sa.ForeignKey('question_response.id', ondelete='CASCADE'), primary_key=True
        ),
        sa.Column('blank_id', sa.Text(), sa.ForeignKey('question_blank.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('answer', sa.Text(), nullable=False, server_default=''),
    )

    op.create_table(
        'question_grading_attempt',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('response_id', sa.Text(), sa.ForeignKey('question_response.id', ondelete='CASCADE'), nullable=False),
        sa.Column('method', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('model_id', sa.Text(), nullable=True),
        sa.Column('awarded_score', sa.Numeric(10, 2), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('error_code', sa.Text(), nullable=True),
        sa.Column('started_at', sa.BigInteger(), nullable=True),
        sa.Column('completed_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index(
        'ix_question_grading_attempt_response_created', 'question_grading_attempt', ['response_id', 'created_at']
    )

    op.create_table(
        'question_score_override',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('response_id', sa.Text(), sa.ForeignKey('question_response.id', ondelete='CASCADE'), nullable=False),
        sa.Column('admin_id', sa.Text(), nullable=False),
        sa.Column('previous_score', sa.Numeric(10, 2), nullable=True),
        sa.Column('new_score', sa.Numeric(10, 2), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index(
        'ix_question_score_override_response_created', 'question_score_override', ['response_id', 'created_at']
    )

    with op.batch_alter_table('experiment_session') as batch_op:
        batch_op.alter_column('topic_id', existing_type=sa.Text(), nullable=True)
        batch_op.alter_column('topic_title', existing_type=sa.Text(), nullable=True)
        batch_op.alter_column('topic_question', existing_type=sa.Text(), nullable=True)
        batch_op.add_column(sa.Column('plan_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('task_type', sa.Text(), nullable=False, server_default='ESSAY'))
        batch_op.add_column(sa.Column('task_submitted_at', sa.BigInteger(), nullable=True))
    with op.batch_alter_table('essay') as batch_op:
        batch_op.add_column(sa.Column('experiment_session_task_id', sa.Text(), nullable=True))
    op.create_index('ix_essay_session_task', 'essay', ['experiment_session_task_id'])
    with op.batch_alter_table('chat') as batch_op:
        batch_op.add_column(sa.Column('experiment_session_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('experiment_session_task_id', sa.Text(), nullable=True))
    op.create_index('ix_chat_experiment_session_task', 'chat', ['experiment_session_id', 'experiment_session_task_id'])
    with op.batch_alter_table('chat_message') as batch_op:
        batch_op.add_column(sa.Column('experiment_session_task_id', sa.Text(), nullable=True))
    op.create_index('ix_chat_message_experiment_task', 'chat_message', ['experiment_session_task_id'])
    with op.batch_alter_table('experiment_telemetry_event') as batch_op:
        batch_op.add_column(sa.Column('session_task_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('question_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('submission_id', sa.Text(), nullable=True))
    op.create_index(
        'ix_experiment_telemetry_event_task_question', 'experiment_telemetry_event', ['session_task_id', 'question_id']
    )

    connection = op.get_bind()
    now = int(time.time_ns())
    plans = {}
    items = {}
    groups = connection.execute(sa.text('SELECT id, data FROM "group"')).fetchall()
    for group_id, data in groups:
        config = _config(data)
        if not config.get('experiment_mode_enabled', False):
            continue
        plan_id, item_id = _uuid(), _uuid()
        plans[group_id], items[group_id] = plan_id, item_id
        mode = config.get('essay_topic_mode', 'random')
        connection.execute(
            sa.text(
                'INSERT INTO experiment_plan '
                '(id, group_id, version, status, progression_mode, chat_mode, survey_variant, locked_at, created_at, updated_at) '
                'VALUES (:id, :group_id, 1, :status, :progression, :chat, :survey, NULL, :now, :now)'
            ),
            {
                'id': plan_id,
                'group_id': group_id,
                'status': 'PUBLISHED',
                'progression': 'STRICT_SEQUENTIAL',
                'chat': 'SHARED_EXPERIMENT',
                'survey': 'ESSAY',
                'now': now,
            },
        )
        connection.execute(
            sa.text(
                'INSERT INTO experiment_plan_item '
                '(id, plan_id, position, task_type, title, question_task_id, essay_topic_mode, essay_topic_id) '
                'VALUES (:id, :plan_id, 0, :task_type, :title, NULL, :mode, :topic_id)'
            ),
            {
                'id': item_id,
                'plan_id': plan_id,
                'task_type': 'ESSAY',
                'title': 'Essay',
                'mode': 'SPECIFIC' if mode == 'specific' else 'RANDOM_ALL',
                'topic_id': config.get('essay_topic_id') if mode == 'specific' else None,
            },
        )

    sessions = connection.execute(
        sa.text(
            'SELECT id, group_id, topic_id, topic_title, topic_question, essay_id, state, created_at, updated_at FROM experiment_session'
        )
    ).fetchall()
    for session in sessions:
        session_id, group_id, topic_id, topic_title, topic_question, essay_id, state, created_at, updated_at = session
        plan_id, item_id = plans.get(group_id), items.get(group_id)
        if not plan_id:
            continue
        session_task_id = _uuid()
        finalized = bool(essay_id)
        status_value = 'FINALIZED' if finalized else ('ACTIVE' if state == 'IN_PROGRESS' else 'LOCKED')
        connection.execute(
            sa.text('UPDATE experiment_session SET plan_id = :plan_id WHERE id = :id'),
            {'plan_id': plan_id, 'id': session_id},
        )
        connection.execute(
            sa.text('UPDATE experiment_plan SET locked_at = :now WHERE id = :id'), {'now': now, 'id': plan_id}
        )
        connection.execute(
            sa.text(
                'INSERT INTO experiment_session_task '
                '(id, experiment_session_id, plan_item_id, position, task_type, title, status, essay_topic_id, essay_topic_title, essay_topic_question, question_task_id, essay_id, essay_draft, started_at, completed_at, finalized_at, created_at, updated_at) '
                'VALUES (:id, :session_id, :item_id, 0, :task_type, :title, :status, :topic_id, :topic_title, :topic_question, NULL, :essay_id, NULL, NULL, :completed, :finalized, :created, :updated)'
            ),
            {
                'id': session_task_id,
                'session_id': session_id,
                'item_id': item_id,
                'task_type': 'ESSAY',
                'title': topic_title or 'Essay',
                'status': status_value,
                'topic_id': topic_id,
                'topic_title': topic_title,
                'topic_question': topic_question,
                'essay_id': essay_id,
                'completed': updated_at if finalized else None,
                'finalized': updated_at if finalized else None,
                'created': created_at,
                'updated': updated_at,
            },
        )
        if essay_id:
            connection.execute(
                sa.text('UPDATE essay SET experiment_session_task_id = :task_id WHERE id = :essay_id'),
                {'task_id': session_task_id, 'essay_id': essay_id},
            )


def downgrade():
    op.drop_index('ix_experiment_telemetry_event_task_question', table_name='experiment_telemetry_event')
    with op.batch_alter_table('experiment_telemetry_event') as batch_op:
        batch_op.drop_column('submission_id')
        batch_op.drop_column('question_id')
        batch_op.drop_column('session_task_id')
    op.drop_index('ix_chat_message_experiment_task', table_name='chat_message')
    with op.batch_alter_table('chat_message') as batch_op:
        batch_op.drop_column('experiment_session_task_id')
    op.drop_index('ix_chat_experiment_session_task', table_name='chat')
    with op.batch_alter_table('chat') as batch_op:
        batch_op.drop_column('experiment_session_task_id')
        batch_op.drop_column('experiment_session_id')
    op.drop_index('ix_essay_session_task', table_name='essay')
    with op.batch_alter_table('essay') as batch_op:
        batch_op.drop_column('experiment_session_task_id')
    with op.batch_alter_table('experiment_session') as batch_op:
        batch_op.drop_column('task_submitted_at')
        batch_op.drop_column('task_type')
        batch_op.drop_column('plan_id')
        batch_op.alter_column('topic_question', existing_type=sa.Text(), nullable=False)
        batch_op.alter_column('topic_title', existing_type=sa.Text(), nullable=False)
        batch_op.alter_column('topic_id', existing_type=sa.Text(), nullable=False)
    for table in (
        'question_score_override',
        'question_grading_attempt',
        'question_response_blank',
        'question_response_choice',
        'question_response',
        'question_submission',
        'experiment_session_task',
        'experiment_plan_item_topic',
        'experiment_plan_item',
        'experiment_plan',
        'question_free_text_config',
        'question_blank_accepted_answer',
        'question_blank',
        'question_choice',
        'question_task_question',
        'question_task',
    ):
        op.drop_table(table)
