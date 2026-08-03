import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_webui.models.experiment_plans import ExperimentSessionTask
from open_webui.models.question_submissions import (
    QuestionGradingAttempt,
    QuestionResponse,
    QuestionSubmission,
)
from open_webui.models.question_tasks import GradingStatus, QuestionTaskModel
from open_webui.utils.question_grading import grade_response


def run(coro):
    return asyncio.run(coro)


def scalar_result(first=None, rows=None):
    scalars = MagicMock()
    scalars.first.return_value = first
    scalars.all.return_value = rows or []
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def grading_task():
    return QuestionTaskModel.model_validate(
        {
            'id': 'task',
            'family_id': 'family',
            'version': 1,
            'latest_version': 1,
            'title': 'Free text',
            'description': '',
            'status': 'PUBLISHED',
            'created_at': 1,
            'updated_at': 1,
            'questions': [
                {
                    'id': 'question',
                    'title': 'Explain photosynthesis',
                    'description': 'Give the main inputs and output.',
                    'question_type': 'FREE_TEXT',
                    'position': 0,
                    'max_score': 5,
                    'grading_mode': 'LLM_ASSISTED',
                    'expected_answer': 'Plants use light, carbon dioxide, and water to produce glucose.',
                    'strictness': 'BALANCED',
                }
            ],
        }
    )


def grading_objects(method='LLM_ASSISTED', effective=None):
    response = QuestionResponse(
        id='response',
        submission_id='submission',
        question_id='question',
        free_text_answer='Plants turn sunlight, water, and carbon dioxide into sugar.',
        is_answered=True,
        grading_status=GradingStatus.PENDING.value,
        grading_method=method,
        effective_score=effective,
        created_at=1,
        updated_at=1,
    )
    attempt = QuestionGradingAttempt(
        id='attempt',
        response_id=response.id,
        method='LLM_ASSISTED',
        status=GradingStatus.PENDING.value,
        created_at=1,
    )
    submission = QuestionSubmission(
        id='submission',
        session_task_id='session-task',
        user_id='participant',
        status='FINALIZED',
        grading_status=GradingStatus.PENDING.value,
        current_score=0,
        maximum_score=5,
        created_at=1,
        updated_at=1,
    )
    session_task = ExperimentSessionTask(
        id='session-task',
        experiment_session_id='session',
        position=0,
        task_type='QUESTION',
        title='Free text',
        status='FINALIZED',
        question_task_id='task',
        created_at=1,
        updated_at=1,
    )
    return response, attempt, submission, session_task


def setup_grader(monkeypatch, output, *, model_available=True, method='LLM_ASSISTED', effective=None):
    response, attempt, submission, session_task = grading_objects(method, effective)
    db = AsyncMock()

    async def get(model, identifier):
        return {
            QuestionResponse: response,
            QuestionSubmission: submission,
            ExperimentSessionTask: session_task,
        }.get(model)

    db.get.side_effect = get
    db.execute.return_value = scalar_result(first=attempt)

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr('open_webui.utils.question_grading.get_async_db_context', context)
    monkeypatch.setattr(
        'open_webui.utils.question_grading.QuestionTasks.get_task',
        AsyncMock(return_value=grading_task()),
    )
    monkeypatch.setattr(
        'open_webui.utils.question_grading.QuestionSubmissions.recalculate',
        AsyncMock(),
    )
    if isinstance(output, Exception):
        completion = AsyncMock(side_effect=output)
    elif isinstance(output, tuple):
        completion = AsyncMock(side_effect=list(output))
    else:
        completion = AsyncMock(return_value=output)
    monkeypatch.setattr('open_webui.utils.question_grading.generate_chat_completion', completion)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={'grader': {}} if model_available else {},
                config=SimpleNamespace(TASK_MODEL='grader', TASK_MODEL_EXTERNAL=''),
            )
        ),
        state=SimpleNamespace(),
    )
    user = SimpleNamespace(id='participant', role='user')
    return request, user, response, attempt, completion


def test_successful_llm_grade_is_structured_bounded_and_recalculated(monkeypatch):
    output = {
        'choices': [
            {
                'message': {
                    'content': json.dumps(
                        {
                            'awarded_score': 4.25,
                            'maximum_score': 5,
                            'rationale': 'The core process and inputs are correct.',
                            'success': True,
                        }
                    )
                }
            }
        ]
    }
    request, user, response, attempt, completion = setup_grader(monkeypatch, output)

    run(grade_response(request, user, response.id))

    assert response.generated_score == Decimal('4.25')
    assert response.effective_score == Decimal('4.25')
    assert response.grading_status == GradingStatus.GRADED.value
    assert attempt.status == GradingStatus.GRADED.value
    payload = completion.await_args.args[1]
    assert payload['response_format']['type'] == 'json_schema'
    serialized = json.dumps(payload)
    assert 'participant_answer' in serialized
    assert 'chain-of-thought' in serialized
    assert 'experiment' not in serialized.lower()


def test_successful_retry_preserves_an_admin_override(monkeypatch):
    output = {
        'choices': [
            {
                'message': {
                    'content': json.dumps(
                        {
                            'awarded_score': 3,
                            'maximum_score': 5,
                            'rationale': 'Some detail is missing.',
                            'success': True,
                        }
                    )
                }
            }
        ]
    }
    request, user, response, _, _ = setup_grader(
        monkeypatch,
        output,
        method='ADMIN_OVERRIDE',
        effective=Decimal('4.50'),
    )

    run(grade_response(request, user, response.id))

    assert response.generated_score == Decimal('3.00')
    assert response.effective_score == Decimal('4.50')
    assert response.grading_method == 'ADMIN_OVERRIDE'


@pytest.mark.parametrize(
    'content',
    [
        (
            '<think>I should assess the scientific concepts.</think>\n'
            '```json\n'
            '{"Awarded Score": "4.25", "Feedback": "The core process and inputs are correct."}\n'
            '```'
        ),
        'Score: 4.25/5\nRationale: The core process and inputs are correct.',
        [
            {'type': 'text', 'text': {'value': 'Here is the grade:\n'}},
            {
                'type': 'text',
                'text': '{"awarded_score": 4.25, "rationale": "The core process is correct."}',
            },
        ],
    ],
)
def test_common_model_format_variations_are_accepted(monkeypatch, content):
    output = {'choices': [{'message': {'content': content}}]}
    request, user, response, attempt, completion = setup_grader(monkeypatch, output)

    run(grade_response(request, user, response.id))

    assert completion.await_count == 1
    assert response.generated_score == Decimal('4.25')
    assert response.grading_status == GradingStatus.GRADED.value
    assert attempt.status == GradingStatus.GRADED.value


def test_malformed_output_gets_one_corrective_retry(monkeypatch):
    repaired = {
        'choices': [
            {
                'message': {
                    'content': json.dumps(
                        {
                            'awarded_score': 4,
                            'rationale': 'The main concepts are present.',
                        }
                    )
                }
            }
        ]
    }
    request, user, response, attempt, completion = setup_grader(
        monkeypatch,
        (
            {'choices': [{'message': {'content': 'I cannot format that result.'}}]},
            repaired,
        ),
    )

    run(grade_response(request, user, response.id))

    assert completion.await_count == 2
    repair_payload = completion.await_args_list[1].args[1]
    assert 'previous response could not be validated' in repair_payload['messages'][-1]['content']
    assert response.generated_score == Decimal('4.00')
    assert response.grading_status == GradingStatus.GRADED.value
    assert attempt.status == GradingStatus.GRADED.value


@pytest.mark.parametrize(
    ('output', 'model_available', 'error_code'),
    [
        ({'choices': [{'message': {'content': 'not-json'}}]}, True, 'malformed_output'),
        (
            {
                'choices': [
                    {
                        'message': {
                            'content': json.dumps(
                                {
                                    'awarded_score': 9,
                                    'maximum_score': 5,
                                    'rationale': 'Out of bounds.',
                                    'success': True,
                                }
                            )
                        }
                    }
                ]
            },
            True,
            'malformed_output',
        ),
        ({}, False, 'model_unavailable'),
        (asyncio.TimeoutError(), True, 'timeout'),
        (
            {
                'choices': [
                    {
                        'message': {
                            'content': json.dumps(
                                {
                                    'awarded_score': 0,
                                    'maximum_score': 5,
                                    'rationale': 'The provider declined to score this answer.',
                                    'success': False,
                                }
                            )
                        }
                    }
                ]
            },
            True,
            'malformed_output',
        ),
    ],
)
def test_llm_grading_failures_preserve_answer_and_record_retryable_error(
    monkeypatch, output, model_available, error_code
):
    request, user, response, attempt, _ = setup_grader(
        monkeypatch,
        output,
        model_available=model_available,
    )
    original_answer = response.free_text_answer

    run(grade_response(request, user, response.id))

    assert response.free_text_answer == original_answer
    assert response.grading_status == GradingStatus.FAILED.value
    assert response.generated_score is None
    assert response.effective_score is None
    assert attempt.status == GradingStatus.FAILED.value
    assert attempt.error_code == error_code
    assert attempt.model_id == ('grader' if model_available else None)
