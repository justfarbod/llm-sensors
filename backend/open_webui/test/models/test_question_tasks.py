import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from open_webui.models.experiment_plans import ExperimentPlanForm, PlanItemForm
from open_webui.models.question_tasks import (
    QuestionChoice,
    QuestionCloneMode,
    QuestionForm,
    QuestionTask,
    QuestionTaskForm,
    QuestionTaskQuestion,
    QuestionTasks,
    grade_fill_blanks,
    grade_multiple_select,
    grade_single_choice,
)


def run(coro):
    return asyncio.run(coro)


def choice(text, correct=False, id=None):
    return {'id': id, 'text': text, 'is_correct': correct}


def test_single_choice_is_all_or_nothing_and_rejects_extra_selections():
    assert grade_single_choice(Decimal('2.50'), {'correct'}, 'correct') == Decimal('2.50')
    assert grade_single_choice(Decimal('2.50'), set(), 'correct') == Decimal('0.00')
    assert grade_single_choice(Decimal('2.50'), {'correct', 'other'}, 'correct') == Decimal('0.00')


def test_multiple_select_formula_rewards_correct_and_penalizes_incorrect():
    all_ids = {'a', 'b', 'c', 'd'}
    correct = {'a', 'b'}

    assert grade_multiple_select(Decimal('10'), {'a', 'b'}, correct, all_ids) == Decimal('10.00')
    assert grade_multiple_select(Decimal('10'), {'a'}, correct, all_ids) == Decimal('5.00')
    assert grade_multiple_select(Decimal('10'), {'a', 'c'}, correct, all_ids) == Decimal('0.00')
    assert grade_multiple_select(Decimal('10'), all_ids, correct, all_ids) == Decimal('0.00')
    assert grade_multiple_select(Decimal('3.33'), {'a'}, correct, all_ids) == Decimal('1.67')


def test_fill_blank_uses_trim_casefold_and_equal_partial_credit():
    accepted = {'one': ['Berlin'], 'two': ['Spree', 'the Spree']}

    assert grade_fill_blanks(Decimal('5'), {'one': ' berlin ', 'two': 'SPREE'}, accepted, False) == Decimal('5.00')
    assert grade_fill_blanks(Decimal('5'), {'one': 'Berlin', 'two': 'wrong'}, accepted, False) == Decimal('2.50')
    assert grade_fill_blanks(Decimal('5'), {'one': 'berlin', 'two': 'Spree'}, accepted, True) == Decimal('2.50')


def test_question_configuration_validation_covers_every_supported_type():
    single = QuestionForm.model_validate(
        {
            'title': 'Single',
            'question_type': 'SINGLE_CHOICE',
            'max_score': '1.25',
            'grading_mode': 'AUTOMATIC',
            'choices': [choice('A', True), choice('B')],
        }
    )
    multiple = QuestionForm.model_validate(
        {
            'title': 'Multiple',
            'question_type': 'MULTIPLE_SELECT',
            'max_score': 2,
            'grading_mode': 'AUTOMATIC',
            'choices': [choice('A', True), choice('B', True), choice('C')],
        }
    )
    fill = QuestionForm.model_validate(
        {
            'title': 'Fill',
            'description': 'The capital is {{capital}} on the {{river}}.',
            'question_type': 'FILL_BLANK',
            'max_score': 2,
            'grading_mode': 'AUTOMATIC',
            'blanks': [
                {'key': 'capital', 'accepted_answers': ['Berlin']},
                {'key': 'river', 'accepted_answers': ['Spree']},
            ],
        }
    )
    free = QuestionForm.model_validate(
        {
            'title': 'Explain',
            'question_type': 'FREE_TEXT',
            'max_score': 5,
            'grading_mode': 'LLM_ASSISTED',
            'expected_answer': 'A grounded expected answer.',
            'strictness': 'STRICT',
        }
    )

    assert [single.question_type, multiple.question_type, fill.question_type, free.question_type]


@pytest.mark.parametrize(
    'payload',
    [
        {
            'title': 'Bad single',
            'question_type': 'SINGLE_CHOICE',
            'max_score': 1,
            'grading_mode': 'AUTOMATIC',
            'choices': [choice('A', True), choice('B', True)],
        },
        {
            'title': 'Bad blank',
            'description': 'Only {{first}} appears.',
            'question_type': 'FILL_BLANK',
            'max_score': 1,
            'grading_mode': 'AUTOMATIC',
            'blanks': [
                {'key': 'first', 'accepted_answers': ['one']},
                {'key': 'second', 'accepted_answers': ['two']},
            ],
        },
        {
            'title': 'Bad free text',
            'question_type': 'FREE_TEXT',
            'max_score': 1,
            'grading_mode': 'LLM_ASSISTED',
            'strictness': 'BALANCED',
        },
        {
            'title': 'Too precise',
            'question_type': 'SINGLE_CHOICE',
            'max_score': '1.001',
            'grading_mode': 'AUTOMATIC',
            'choices': [choice('A', True), choice('B')],
        },
    ],
)
def test_invalid_question_configuration_is_rejected(payload):
    with pytest.raises(ValidationError):
        QuestionForm.model_validate(payload)


def test_participant_projection_never_exposes_answer_keys(monkeypatch):
    admin_task = QuestionTaskForm.model_validate(
        {
            'title': 'Secrets',
            'questions': [
                {
                    'title': 'Choose',
                    'question_type': 'SINGLE_CHOICE',
                    'max_score': 1,
                    'grading_mode': 'AUTOMATIC',
                    'choices': [choice('Correct', True, 'correct'), choice('Wrong', False, 'wrong')],
                }
            ],
        }
    )
    # The participant projection is deliberately a different schema; exercise it
    # with the same shape returned by the admin task loader.
    from open_webui.models.question_tasks import QuestionTaskModel

    task = QuestionTaskModel.model_validate(
        {
            'id': 'task',
            'family_id': 'family',
            'version': 1,
            'latest_version': 1,
            'title': admin_task.title,
            'description': '',
            'status': 'PUBLISHED',
            'created_at': 1,
            'updated_at': 1,
            'questions': [
                {
                    'id': 'question',
                    'title': 'Choose',
                    'description': '',
                    'question_type': 'SINGLE_CHOICE',
                    'position': 0,
                    'max_score': 1,
                    'grading_mode': 'AUTOMATIC',
                    'choices': [
                        {'id': 'correct', 'text': 'Correct', 'position': 0, 'is_correct': True},
                        {'id': 'wrong', 'text': 'Wrong', 'position': 1, 'is_correct': False},
                    ],
                }
            ],
        }
    )

    async def fake_get_task(task_id, db=None):
        return task

    monkeypatch.setattr(QuestionTasks, 'get_task', fake_get_task)
    import asyncio

    participant = asyncio.run(QuestionTasks.participant_task('task'))
    payload = participant.model_dump()
    assert 'grading_mode' not in payload['questions'][0]
    assert 'is_correct' not in payload['questions'][0]['choices'][0]
    assert 'expected_answer' not in payload['questions'][0]


def test_experiment_plan_requires_valid_task_specific_fields():
    form = ExperimentPlanForm.model_validate(
        {
            'progression_mode': 'SEQUENTIAL_REVIEW',
            'chat_mode': 'FRESH_PER_TASK',
            'items': [
                {'task_type': 'QUESTION', 'title': 'Quiz', 'question_task_id': 'question-task'},
                {
                    'task_type': 'ESSAY',
                    'title': 'Essay',
                    'essay_topic_mode': 'RANDOM_SELECTED',
                    'essay_topic_ids': ['one', 'two'],
                },
            ],
        }
    )
    assert len(form.items) == 2

    with pytest.raises(ValidationError):
        PlanItemForm.model_validate({'task_type': 'QUESTION', 'title': 'Missing task'})
    with pytest.raises(ValidationError):
        PlanItemForm.model_validate(
            {
                'task_type': 'ESSAY',
                'title': 'Pool too small',
                'essay_topic_mode': 'RANDOM_SELECTED',
                'essay_topic_ids': ['only-one'],
            }
        )


def test_question_order_and_choice_order_are_persisted_from_form_order():
    form = QuestionTaskForm.model_validate(
        {
            'title': 'Ordered',
            'questions': [
                {
                    'id': 'second-now-first',
                    'title': 'Displayed first',
                    'question_type': 'SINGLE_CHOICE',
                    'max_score': 1,
                    'grading_mode': 'AUTOMATIC',
                    'choices': [
                        choice('Displayed first', True, 'choice-first'),
                        choice('Displayed second', False, 'choice-second'),
                    ],
                },
                {
                    'id': 'first-now-second',
                    'title': 'Displayed second',
                    'question_type': 'SINGLE_CHOICE',
                    'max_score': 1,
                    'grading_mode': 'AUTOMATIC',
                    'choices': [choice('A', True), choice('B')],
                },
            ],
        }
    )
    db = AsyncMock()
    db.add = MagicMock()
    task = QuestionTask(id='task', title='Ordered', description='', status='DRAFT', created_at=1, updated_at=1)

    run(QuestionTasks._replace_questions(task, form.questions, db))

    questions = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], QuestionTaskQuestion)]
    choices = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], QuestionChoice)]
    assert [(item.id, item.position) for item in questions] == [
        ('second-now-first', 0),
        ('first-now-second', 1),
    ]
    assert [(item.id, item.position) for item in choices[:2]] == [
        ('choice-first', 0),
        ('choice-second', 1),
    ]


def test_draft_question_task_edit_replaces_questions_and_archive_is_soft_delete(monkeypatch):
    task = QuestionTask(
        id='task',
        title='Old title',
        description='Old description',
        status='DRAFT',
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.get.return_value = task

    @asynccontextmanager
    async def context(passed=None):
        yield passed or db

    delete_questions = AsyncMock()
    replace_questions = AsyncMock()
    updated = object()
    monkeypatch.setattr('open_webui.models.question_tasks.get_async_db_context', context)
    monkeypatch.setattr(QuestionTasks, '_delete_questions', delete_questions)
    monkeypatch.setattr(QuestionTasks, '_replace_questions', replace_questions)
    monkeypatch.setattr(QuestionTasks, 'get_task', AsyncMock(return_value=updated))
    form = QuestionTaskForm.model_validate(
        {
            'title': 'Updated title',
            'description': 'Updated description',
            'questions': [
                {
                    'title': 'Question',
                    'question_type': 'SINGLE_CHOICE',
                    'max_score': 1,
                    'grading_mode': 'AUTOMATIC',
                    'choices': [choice('A', True), choice('B')],
                }
            ],
        }
    )

    assert run(QuestionTasks.update_task(task.id, form, db=db)) is updated
    assert task.title == 'Updated title'
    assert task.description == 'Updated description'
    assert task.status == 'DRAFT'
    delete_questions.assert_awaited_once_with(task.id, db)
    replace_questions.assert_awaited_once_with(task, form.questions, db)

    # Archive retains the row and its referenced results while removing it from
    # the default authoring list.
    QuestionTasks.get_task = AsyncMock(return_value=updated)
    assert run(QuestionTasks.archive(task.id, db=db)) is True
    assert task.status == 'ARCHIVED'
    assert task.archived_at is not None


def test_locked_question_task_cannot_be_edited(monkeypatch):
    task = QuestionTask(
        id='task',
        title='Locked',
        description='',
        status='PUBLISHED',
        locked_at=2,
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.get.return_value = task

    @asynccontextmanager
    async def context(passed=None):
        yield passed or db

    monkeypatch.setattr('open_webui.models.question_tasks.get_async_db_context', context)
    with pytest.raises(Exception) as exc:
        run(QuestionTasks.update_task(task.id, QuestionTaskForm(title='No change'), db=db))
    assert getattr(exc.value, 'status_code', None) == 409


def test_published_question_task_requires_version_or_duplicate(monkeypatch):
    task = QuestionTask(
        id='task',
        family_id='family',
        version=1,
        title='Published',
        description='',
        status='PUBLISHED',
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.get.return_value = task

    @asynccontextmanager
    async def context(passed=None):
        yield passed or db

    monkeypatch.setattr('open_webui.models.question_tasks.get_async_db_context', context)
    with pytest.raises(Exception) as exc:
        run(QuestionTasks.update_task(task.id, QuestionTaskForm(title='No change'), db=db))
    assert getattr(exc.value, 'status_code', None) == 409


def test_edit_as_new_version_preserves_family_and_deep_clones_questions(monkeypatch):
    from open_webui.models.question_tasks import QuestionTaskModel

    source = QuestionTaskModel.model_validate(
        {
            'id': 'source',
            'family_id': 'family',
            'version': 2,
            'latest_version': 2,
            'title': 'Versioned task',
            'description': 'Description',
            'status': 'PUBLISHED',
            'created_at': 1,
            'updated_at': 1,
            'questions': [
                {
                    'id': 'question',
                    'title': 'Choose',
                    'description': '',
                    'question_type': 'SINGLE_CHOICE',
                    'position': 0,
                    'max_score': 1,
                    'grading_mode': 'AUTOMATIC',
                    'image_file_id': 'image',
                    'choices': [
                        {'id': 'a', 'text': 'A', 'position': 0, 'is_correct': True},
                        {'id': 'b', 'text': 'B', 'position': 1, 'is_correct': False},
                    ],
                }
            ],
        }
    )
    cloned = object()
    db = AsyncMock()
    db.add = MagicMock()
    max_result = MagicMock()
    max_result.scalar.return_value = 2
    db.execute.return_value = max_result

    @asynccontextmanager
    async def context(passed=None):
        yield passed or db

    monkeypatch.setattr('open_webui.models.question_tasks.get_async_db_context', context)
    monkeypatch.setattr(QuestionTasks, 'get_task', AsyncMock(side_effect=[source, cloned]))
    replace = AsyncMock()
    monkeypatch.setattr(QuestionTasks, '_replace_questions', replace)

    assert run(QuestionTasks.clone(source.id, QuestionCloneMode.VERSION, db=db)) is cloned

    row = db.add.call_args.args[0]
    assert row.family_id == source.family_id
    assert row.version == 3
    assert row.status == 'DRAFT'
    forms = replace.await_args.args[1]
    assert forms[0].image_file_id == 'image'
    assert [(item.text, item.is_correct) for item in forms[0].choices] == [('A', True), ('B', False)]
