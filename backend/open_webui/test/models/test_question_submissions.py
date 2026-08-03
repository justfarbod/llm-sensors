import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from open_webui.models.experiment_plans import ExperimentSessionTask
from open_webui.models.question_submissions import (
    QuestionResponse,
    QuestionScoreOverride,
    QuestionSubmission,
    QuestionSubmissions,
)
from open_webui.models.question_tasks import GradingStatus, QuestionTaskModel
from open_webui.routers.experiment_analytics import (
    ScoreOverrideForm,
    override_question_score,
    retry_question_grading,
)


def run(coro):
    return asyncio.run(coro)


def scalar_result(rows=None):
    scalars = MagicMock()
    scalars.all.return_value = rows or []
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def free_text_task(grading_mode='MANUAL'):
    question = {
        'id': 'question',
        'title': 'Explain',
        'description': '',
        'question_type': 'FREE_TEXT',
        'position': 0,
        'max_score': 5,
        'grading_mode': grading_mode,
    }
    if grading_mode == 'LLM_ASSISTED':
        question.update(expected_answer='Expected', strictness='BALANCED')
    return QuestionTaskModel.model_validate(
        {
            'id': 'task',
            'family_id': 'family',
            'version': 1,
            'latest_version': 1,
            'title': 'Task',
            'description': '',
            'status': 'PUBLISHED',
            'created_at': 1,
            'updated_at': 1,
            'questions': [question],
        }
    )


def submission_objects():
    submission = QuestionSubmission(
        id='submission',
        session_task_id='session-task',
        user_id='participant',
        status='DRAFT',
        grading_status='NOT_STARTED',
        current_score=0,
        maximum_score=5,
        created_at=1,
        updated_at=1,
    )
    response = QuestionResponse(
        id='response',
        submission_id=submission.id,
        question_id='question',
        free_text_answer='',
        is_answered=False,
        grading_status='NOT_STARTED',
        created_at=1,
        updated_at=1,
    )
    task = ExperimentSessionTask(
        id='session-task',
        experiment_session_id='session',
        position=0,
        task_type='QUESTION',
        title='Task',
        status='AVAILABLE',
        question_task_id='task',
        created_at=1,
        updated_at=1,
    )
    return submission, response, task


def test_manual_free_text_finalization_waits_for_admin_review(monkeypatch):
    submission, response, session_task = submission_objects()
    db = AsyncMock()

    async def get(model, identifier):
        return {
            QuestionSubmission: submission,
            ExperimentSessionTask: session_task,
        }.get(model)

    db.get.side_effect = get
    db.execute.side_effect = [
        scalar_result([response]),
        scalar_result([]),
        scalar_result([]),
        scalar_result([response]),
    ]

    @asynccontextmanager
    async def context():
        raise AssertionError('An explicitly supplied transaction must not be replaced.')
        yield

    monkeypatch.setattr('open_webui.models.question_submissions.get_async_db_context', context)
    monkeypatch.setattr(
        'open_webui.models.question_submissions.QuestionTasks.get_task',
        AsyncMock(return_value=free_text_task()),
    )
    monkeypatch.setattr(
        QuestionSubmissions,
        'participant_model',
        AsyncMock(return_value=SimpleNamespace(id='submission')),
    )

    pending, _ = run(QuestionSubmissions.finalize(submission.id, 'participant', db=db))

    assert pending == []
    assert response.grading_status == GradingStatus.AWAITING_REVIEW.value
    assert response.grading_method == 'MANUAL'
    assert response.generated_score is None
    assert response.effective_score is None
    assert submission.status == 'FINALIZED'
    assert submission.current_score is None
    assert submission.provisional_score == Decimal('0')
    assert submission.grading_status == GradingStatus.AWAITING_REVIEW.value


def test_total_score_recalculation_uses_effective_scores_and_status_priority():
    submission = QuestionSubmission(
        id='submission',
        session_task_id='task',
        user_id='participant',
        status='FINALIZED',
        grading_status='NOT_STARTED',
        current_score=0,
        maximum_score=10,
        created_at=1,
        updated_at=1,
    )
    responses = [
        QuestionResponse(
            id='one',
            submission_id='submission',
            question_id='one',
            grading_status='GRADED',
            effective_score=Decimal('2.25'),
            is_answered=True,
            created_at=1,
            updated_at=1,
        ),
        QuestionResponse(
            id='two',
            submission_id='submission',
            question_id='two',
            grading_status='AWAITING_REVIEW',
            effective_score=None,
            is_answered=False,
            created_at=1,
            updated_at=1,
        ),
        QuestionResponse(
            id='three',
            submission_id='submission',
            question_id='three',
            grading_status='GRADED',
            effective_score=Decimal('3.50'),
            is_answered=True,
            created_at=1,
            updated_at=1,
        ),
    ]
    db = AsyncMock()
    db.get.return_value = submission
    db.execute.return_value = scalar_result(responses)

    run(QuestionSubmissions.recalculate(submission.id, db))

    assert submission.current_score is None
    assert submission.provisional_score == Decimal('5.75')
    assert submission.grading_status == GradingStatus.AWAITING_REVIEW.value

    responses[1].grading_status = GradingStatus.FAILED.value
    run(QuestionSubmissions.recalculate(submission.id, db))
    assert submission.current_score is None
    assert submission.grading_status == GradingStatus.PENDING.value
    assert submission.has_grading_error is True

    responses[1].grading_status = GradingStatus.PENDING.value
    run(QuestionSubmissions.recalculate(submission.id, db))
    assert submission.grading_status == GradingStatus.PENDING.value


@pytest.mark.parametrize(
    ('original_method', 'original_score', 'expected_method'),
    [
        ('MANUAL', None, 'MANUAL'),
        ('AUTOMATIC', Decimal('2.00'), 'ADMIN_OVERRIDE'),
        ('LLM_ASSISTED', Decimal('3.00'), 'ADMIN_OVERRIDE'),
    ],
)
def test_score_override_records_audit_and_preserves_generated_score(
    monkeypatch, original_method, original_score, expected_method
):
    submission, response, session_task = submission_objects()
    response.grading_method = original_method
    response.grading_status = (
        GradingStatus.AWAITING_REVIEW.value if original_method == 'MANUAL' else GradingStatus.GRADED.value
    )
    response.generated_score = original_score
    response.effective_score = original_score
    db = AsyncMock()
    db.add = MagicMock()

    async def get(model, identifier):
        return {
            QuestionResponse: response,
            QuestionSubmission: submission,
            ExperimentSessionTask: session_task,
        }.get(model)

    db.get.side_effect = get
    monkeypatch.setattr(
        'open_webui.routers.experiment_analytics.QuestionTasks.get_task',
        AsyncMock(return_value=free_text_task()),
    )
    recalculate = AsyncMock()
    monkeypatch.setattr(
        'open_webui.routers.experiment_analytics.QuestionSubmissions.recalculate',
        recalculate,
    )
    monkeypatch.setattr(
        'open_webui.routers.experiment_analytics.question_submission_detail',
        AsyncMock(return_value={'submission_id': submission.id}),
    )
    admin = SimpleNamespace(id='admin')

    result = run(
        override_question_score(
            response.id,
            ScoreOverrideForm(score=Decimal('4.50'), note=' Reviewed '),
            admin,
            db,
        )
    )

    audit = db.add.call_args.args[0]
    assert isinstance(audit, QuestionScoreOverride)
    assert audit.admin_id == 'admin'
    assert audit.previous_score == original_score
    assert audit.new_score == Decimal('4.50')
    assert audit.note == 'Reviewed'
    assert response.generated_score == original_score
    assert response.effective_score == Decimal('4.50')
    assert response.grading_method == expected_method
    assert response.grading_status == GradingStatus.GRADED.value
    recalculate.assert_awaited_once_with(submission.id, db)
    assert result == {'submission_id': submission.id}


def test_score_override_cannot_exceed_question_maximum(monkeypatch):
    submission, response, session_task = submission_objects()
    db = AsyncMock()

    async def get(model, identifier):
        return {
            QuestionResponse: response,
            QuestionSubmission: submission,
            ExperimentSessionTask: session_task,
        }.get(model)

    db.get.side_effect = get
    monkeypatch.setattr(
        'open_webui.routers.experiment_analytics.QuestionTasks.get_task',
        AsyncMock(return_value=free_text_task()),
    )
    with pytest.raises(HTTPException) as exc:
        run(
            override_question_score(
                response.id,
                ScoreOverrideForm(score=Decimal('5.01')),
                SimpleNamespace(id='admin'),
                db,
            )
        )
    assert exc.value.status_code == 422


@pytest.mark.parametrize('overridden', [False, True])
def test_retry_immediately_returns_pending_detail_and_preserves_only_override(monkeypatch, overridden):
    submission, response, session_task = submission_objects()
    response.grading_method = 'ADMIN_OVERRIDE' if overridden else 'LLM_ASSISTED'
    response.grading_status = GradingStatus.GRADED.value
    response.generated_score = Decimal('3.00')
    response.effective_score = Decimal('4.00') if overridden else Decimal('3.00')
    response.rationale = 'Previous attempt'
    db = AsyncMock()
    db.add = MagicMock()

    async def get(model, identifier):
        return {
            QuestionResponse: response,
            QuestionSubmission: submission,
            ExperimentSessionTask: session_task,
        }.get(model)

    db.get.side_effect = get
    active_result = MagicMock()
    active_result.scalars.return_value.first.return_value = None
    db.execute.return_value = active_result
    monkeypatch.setattr(
        'open_webui.routers.experiment_analytics.QuestionTasks.get_task',
        AsyncMock(return_value=free_text_task('LLM_ASSISTED')),
    )
    recalculate = AsyncMock()
    monkeypatch.setattr('open_webui.routers.experiment_analytics.QuestionSubmissions.recalculate', recalculate)
    monkeypatch.setattr(
        'open_webui.routers.experiment_analytics.question_submission_detail',
        AsyncMock(return_value={'submission_id': submission.id, 'grading_status': 'PENDING'}),
    )
    scheduled = AsyncMock()
    monkeypatch.setattr('open_webui.routers.experiment_analytics.create_task', scheduled)
    monkeypatch.setattr('open_webui.routers.experiment_analytics.grade_response', lambda *args: 'grading-job')
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))

    result = run(
        retry_question_grading(
            response.id,
            request,
            user=SimpleNamespace(id='admin', role='admin'),
            db=db,
        )
    )

    attempt = db.add.call_args.args[0]
    assert result['status'] == 'PENDING'
    assert result['attempt_id'] == attempt.id
    assert result['submission']['submission_id'] == submission.id
    if overridden:
        assert response.effective_score == Decimal('4.00')
        assert response.grading_method == 'ADMIN_OVERRIDE'
    else:
        assert response.generated_score is None
        assert response.effective_score is None
        assert response.rationale is None
        assert response.grading_status == GradingStatus.PENDING.value
    recalculate.assert_awaited_once_with(submission.id, db)
    scheduled.assert_awaited_once()
