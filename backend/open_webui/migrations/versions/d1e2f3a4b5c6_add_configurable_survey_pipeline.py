"""add configurable survey pipeline and task versioning

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
"""

import time
import uuid

from alembic import op
import sqlalchemy as sa

revision = 'd1e2f3a4b5c6'
down_revision = 'c0d1e2f3a4b5'
branch_labels = None
depends_on = None


def _uuid():
    return str(uuid.uuid4())


LEGACY_SURVEYS = {
    'ESSAY_PRE': (
        'Legacy essay pre-survey',
        [
            ('School class', 'SHORT_TEXT', True, None),
            ('How familiar are you with AI tools?', 'SCALE', True, ('Not familiar at all', 'Very familiar')),
            (
                'How often do you use AI for schoolwork?',
                'SINGLE_CHOICE',
                True,
                ['Never', 'Rarely', 'Sometimes', 'Often', 'Very often'],
            ),
            (
                'How confident are you in your essay-writing skills?',
                'SCALE',
                True,
                ('Not confident at all', 'Very confident'),
            ),
            (
                'Age range',
                'SINGLE_CHOICE',
                True,
                ['Under 13', '13–14', '15–16', '17–18', 'Over 18', 'Prefer not to say'],
            ),
        ],
    ),
    'ESSAY_POST': (
        'Legacy essay post-survey',
        [
            (
                'How helpful was the AI assistant while writing your essay?',
                'SCALE',
                True,
                ('Not helpful at all', 'Extremely helpful'),
            ),
            (
                'How satisfied are you with the essay you submitted?',
                'SCALE',
                True,
                ('Not satisfied at all', 'Very satisfied'),
            ),
            (
                'How much do you think the AI assistant improved your essay?',
                'SINGLE_CHOICE',
                True,
                ['Not at all', 'A little', 'A moderate amount', 'A lot', 'A great deal'],
            ),
            ('How easy was it to use the AI chat while writing?', 'SCALE', True, ('Very difficult', 'Very easy')),
            ('Optional comments', 'LONG_TEXT', False, None),
        ],
    ),
    'TASK_NEUTRAL_PRE': (
        'Legacy task pre-survey',
        [
            ('School class', 'SHORT_TEXT', True, None),
            ('How familiar are you with AI tools?', 'SCALE', True, ('Not familiar at all', 'Very familiar')),
            (
                'How often do you use AI for schoolwork?',
                'SINGLE_CHOICE',
                True,
                ['Never', 'Rarely', 'Sometimes', 'Often', 'Very often'],
            ),
            (
                'How confident are you that you can complete the assigned tasks?',
                'SCALE',
                True,
                ('Not confident at all', 'Very confident'),
            ),
            (
                'Age range',
                'SINGLE_CHOICE',
                True,
                ['Under 13', '13–14', '15–16', '17–18', 'Over 18', 'Prefer not to say'],
            ),
        ],
    ),
    'TASK_NEUTRAL_POST': (
        'Legacy task post-survey',
        [
            (
                'How helpful was the AI assistant while completing the tasks?',
                'SCALE',
                True,
                ('Not helpful at all', 'Extremely helpful'),
            ),
            (
                'How satisfied are you with the work you submitted?',
                'SCALE',
                True,
                ('Not satisfied at all', 'Very satisfied'),
            ),
            (
                'How much do you think the AI assistant improved your submitted work?',
                'SINGLE_CHOICE',
                True,
                ['Not at all', 'A little', 'A moderate amount', 'A lot', 'A great deal'],
            ),
            ('How easy was it to use the AI chat?', 'SCALE', True, ('Very difficult', 'Very easy')),
            ('Optional comments', 'LONG_TEXT', False, None),
        ],
    ),
}


def upgrade():
    op.create_table(
        'survey_task',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('family_id', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.Text(), nullable=False, server_default='DRAFT'),
        sa.Column('locked_at', sa.BigInteger(), nullable=True),
        sa.Column('archived_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('family_id', 'version', name='uq_survey_task_family_version'),
    )
    op.create_index('ix_survey_task_family_status', 'survey_task', ['family_id', 'status'])
    op.create_table(
        'survey_question',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('task_id', sa.Text(), sa.ForeignKey('survey_task.id', ondelete='CASCADE'), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('question_type', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('required', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('scale_low_label', sa.Text(), nullable=True),
        sa.Column('scale_high_label', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('task_id', 'position', name='uq_survey_question_task_position'),
    )
    op.create_index('ix_survey_question_task', 'survey_question', ['task_id'])
    op.create_table(
        'survey_choice',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('question_id', sa.Text(), sa.ForeignKey('survey_question.id', ondelete='CASCADE'), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.UniqueConstraint('question_id', 'position', name='uq_survey_choice_question_position'),
    )
    op.create_index('ix_survey_choice_question', 'survey_choice', ['question_id'])

    with op.batch_alter_table('question_task') as batch_op:
        batch_op.add_column(sa.Column('family_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.execute('UPDATE question_task SET family_id = id WHERE family_id IS NULL')
    with op.batch_alter_table('question_task') as batch_op:
        batch_op.alter_column('family_id', existing_type=sa.Text(), nullable=False)
        batch_op.create_unique_constraint('uq_question_task_family_version', ['family_id', 'version'])
    op.create_index('ix_question_task_family_status', 'question_task', ['family_id', 'status'])

    with op.batch_alter_table('experiment_plan') as batch_op:
        batch_op.add_column(sa.Column('consent_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table('experiment_plan_item') as batch_op:
        batch_op.add_column(sa.Column('survey_task_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('survey_required', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.create_foreign_key(
            'fk_experiment_plan_item_survey_task', 'survey_task', ['survey_task_id'], ['id'], ondelete='RESTRICT'
        )
    with op.batch_alter_table('experiment_session_task') as batch_op:
        batch_op.add_column(sa.Column('survey_task_id', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('survey_required', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.create_foreign_key(
            'fk_experiment_session_task_survey_task', 'survey_task', ['survey_task_id'], ['id'], ondelete='RESTRICT'
        )

    op.create_table(
        'survey_submission',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'session_task_id',
            sa.Text(),
            sa.ForeignKey('experiment_session_task.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='DRAFT'),
        sa.Column('submitted_at', sa.BigInteger(), nullable=True),
        sa.Column('skipped_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('session_task_id', name='uq_survey_submission_session_task'),
    )
    op.create_index('ix_survey_submission_user_status', 'survey_submission', ['user_id', 'status'])
    op.create_table(
        'survey_response',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'submission_id', sa.Text(), sa.ForeignKey('survey_submission.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column('question_id', sa.Text(), sa.ForeignKey('survey_question.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('text_answer', sa.Text(), nullable=True),
        sa.Column('scale_answer', sa.Integer(), nullable=True),
        sa.Column('is_answered', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('submission_id', 'question_id', name='uq_survey_response_submission_question'),
    )
    op.create_index('ix_survey_response_submission', 'survey_response', ['submission_id'])
    op.create_table(
        'survey_response_choice',
        sa.Column('response_id', sa.Text(), sa.ForeignKey('survey_response.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('choice_id', sa.Text(), sa.ForeignKey('survey_choice.id', ondelete='RESTRICT'), primary_key=True),
    )

    with op.batch_alter_table('question_submission') as batch_op:
        batch_op.alter_column('current_score', existing_type=sa.Numeric(10, 2), nullable=True)
        batch_op.add_column(sa.Column('provisional_score', sa.Numeric(10, 2), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('has_grading_error', sa.Boolean(), nullable=False, server_default=sa.false()))

    connection = op.get_bind()
    now = int(time.time_ns())
    survey_ids = {}
    for key, (title, questions) in LEGACY_SURVEYS.items():
        task_id = _uuid()
        survey_ids[key] = task_id
        connection.execute(
            sa.text(
                'INSERT INTO survey_task '
                '(id, family_id, version, title, description, status, locked_at, archived_at, created_at, updated_at) '
                'VALUES (:id, :id, 1, :title, :description, :status, NULL, NULL, :now, :now)'
            ),
            {
                'id': task_id,
                'title': title,
                'description': 'Migrated from the original experiment survey.',
                'status': 'PUBLISHED',
                'now': now,
            },
        )
        for position, (prompt, question_type, required, config) in enumerate(questions):
            question_id = _uuid()
            low = config[0] if question_type == 'SCALE' else None
            high = config[1] if question_type == 'SCALE' else None
            connection.execute(
                sa.text(
                    'INSERT INTO survey_question '
                    '(id, task_id, prompt, description, question_type, position, required, enabled, scale_low_label, scale_high_label, created_at, updated_at) '
                    'VALUES (:id, :task, :prompt, :description, :type, :position, :required, :enabled, :low, :high, :now, :now)'
                ),
                {
                    'id': question_id,
                    'task': task_id,
                    'prompt': prompt,
                    'description': '',
                    'type': question_type,
                    'position': position,
                    'required': required,
                    'enabled': True,
                    'low': low,
                    'high': high,
                    'now': now,
                },
            )
            if question_type == 'SINGLE_CHOICE':
                for choice_position, text in enumerate(config):
                    connection.execute(
                        sa.text(
                            'INSERT INTO survey_choice (id, question_id, text, position) VALUES (:id, :question, :text, :position)'
                        ),
                        {'id': _uuid(), 'question': question_id, 'text': text, 'position': choice_position},
                    )

    plans = connection.execute(
        sa.text(
            "SELECT id, group_id, version, progression_mode, chat_mode, survey_variant "
            "FROM experiment_plan WHERE status = 'PUBLISHED'"
        )
    ).fetchall()
    for old_id, group_id, version, progression, chat_mode, survey_variant in plans:
        new_id = _uuid()
        variant = survey_variant if survey_variant in {'ESSAY', 'TASK_NEUTRAL'} else 'TASK_NEUTRAL'
        next_version = connection.execute(
            sa.text('SELECT COALESCE(MAX(version), 0) + 1 FROM experiment_plan WHERE group_id = :group'),
            {'group': group_id},
        ).scalar_one()
        connection.execute(
            sa.text("UPDATE experiment_plan SET status = 'SUPERSEDED', updated_at = :now WHERE id = :id"),
            {'now': now, 'id': old_id},
        )
        connection.execute(
            sa.text(
                'INSERT INTO experiment_plan '
                '(id, group_id, version, status, progression_mode, chat_mode, survey_variant, consent_enabled, locked_at, created_at, updated_at) '
                'VALUES (:id, :group, :version, :status, :progression, :chat, :variant, :consent, NULL, :now, :now)'
            ),
            {
                'id': new_id,
                'group': group_id,
                'version': next_version,
                'status': 'PUBLISHED',
                'progression': progression,
                'chat': chat_mode,
                'variant': variant,
                'consent': True,
                'now': now,
            },
        )
        old_items = connection.execute(
            sa.text(
                'SELECT id, position, task_type, title, question_task_id, essay_topic_mode, essay_topic_id '
                'FROM experiment_plan_item WHERE plan_id = :plan ORDER BY position'
            ),
            {'plan': old_id},
        ).fetchall()
        pre_item = _uuid()
        post_item = _uuid()
        for item_id, position, task_type, title, question_task_id, essay_topic_mode, essay_topic_id in old_items:
            copied_id = _uuid()
            connection.execute(
                sa.text(
                    'INSERT INTO experiment_plan_item '
                    '(id, plan_id, position, task_type, title, question_task_id, essay_topic_mode, essay_topic_id, survey_task_id, survey_required, enabled) '
                    'VALUES (:id, :plan, :position, :type, :title, :question, :mode, :topic, NULL, :required, :enabled)'
                ),
                {
                    'id': copied_id,
                    'plan': new_id,
                    'position': position + 1,
                    'type': task_type,
                    'title': title,
                    'question': question_task_id,
                    'mode': essay_topic_mode,
                    'topic': essay_topic_id,
                    'required': True,
                    'enabled': True,
                },
            )
            pools = connection.execute(
                sa.text('SELECT topic_id FROM experiment_plan_item_topic WHERE plan_item_id = :item'),
                {'item': item_id},
            ).fetchall()
            for (topic_id,) in pools:
                connection.execute(
                    sa.text('INSERT INTO experiment_plan_item_topic (plan_item_id, topic_id) VALUES (:item, :topic)'),
                    {'item': copied_id, 'topic': topic_id},
                )
        connection.execute(
            sa.text(
                'INSERT INTO experiment_plan_item '
                '(id, plan_id, position, task_type, title, question_task_id, essay_topic_mode, essay_topic_id, survey_task_id, survey_required, enabled) '
                'VALUES (:id, :plan, 0, :type, :title, NULL, NULL, NULL, :survey, :required, :enabled)'
            ),
            {
                'id': pre_item,
                'plan': new_id,
                'type': 'SURVEY',
                'title': LEGACY_SURVEYS[f'{variant}_PRE'][0],
                'survey': survey_ids[f'{variant}_PRE'],
                'required': True,
                'enabled': True,
            },
        )
        connection.execute(
            sa.text(
                'INSERT INTO experiment_plan_item '
                '(id, plan_id, position, task_type, title, question_task_id, essay_topic_mode, essay_topic_id, survey_task_id, survey_required, enabled) '
                'VALUES (:id, :plan, :position, :type, :title, NULL, NULL, NULL, :survey, :required, :enabled)'
            ),
            {
                'id': post_item,
                'plan': new_id,
                'position': len(old_items) + 1,
                'type': 'SURVEY',
                'title': LEGACY_SURVEYS[f'{variant}_POST'][0],
                'survey': survey_ids[f'{variant}_POST'],
                'required': True,
                'enabled': True,
            },
        )


def downgrade():
    op.execute(
        'UPDATE question_submission '
        'SET current_score = COALESCE(current_score, provisional_score, 0) '
        'WHERE current_score IS NULL'
    )
    with op.batch_alter_table('question_submission') as batch_op:
        batch_op.drop_column('has_grading_error')
        batch_op.drop_column('provisional_score')
        batch_op.alter_column('current_score', existing_type=sa.Numeric(10, 2), nullable=False, server_default='0')
    op.drop_table('survey_response_choice')
    op.drop_index('ix_survey_response_submission', table_name='survey_response')
    op.drop_table('survey_response')
    op.drop_index('ix_survey_submission_user_status', table_name='survey_submission')
    op.drop_table('survey_submission')
    with op.batch_alter_table('experiment_session_task') as batch_op:
        batch_op.drop_constraint('fk_experiment_session_task_survey_task', type_='foreignkey')
        batch_op.drop_column('survey_required')
        batch_op.drop_column('survey_task_id')
    with op.batch_alter_table('experiment_plan_item') as batch_op:
        batch_op.drop_constraint('fk_experiment_plan_item_survey_task', type_='foreignkey')
        batch_op.drop_column('enabled')
        batch_op.drop_column('survey_required')
        batch_op.drop_column('survey_task_id')
    with op.batch_alter_table('experiment_plan') as batch_op:
        batch_op.drop_column('consent_enabled')
    op.drop_index('ix_question_task_family_status', table_name='question_task')
    with op.batch_alter_table('question_task') as batch_op:
        batch_op.drop_constraint('uq_question_task_family_version', type_='unique')
        batch_op.drop_column('version')
        batch_op.drop_column('family_id')
    op.drop_index('ix_survey_choice_question', table_name='survey_choice')
    op.drop_table('survey_choice')
    op.drop_index('ix_survey_question_task', table_name='survey_question')
    op.drop_table('survey_question')
    op.drop_index('ix_survey_task_family_status', table_name='survey_task')
    op.drop_table('survey_task')
