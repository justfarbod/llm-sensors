import asyncio
import importlib
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.responses import JSONResponse, StreamingResponse
from starlette.requests import Request

from open_webui.models.experiment_plans import PlanItemForm
from open_webui.models.experiment_prompt_budgets import (
    ExperimentPromptBucket as Bucket,
    ExperimentPromptReservation as Reservation,
    LLMPromptBudget,
    default_prompt_budget,
    remap_prompt_budget,
)
from open_webui.utils import experiment_prompt_budgets as budgets


def task(task_id='task', limit=2, questions=None):
    return SimpleNamespace(
        id=task_id,
        task_type='QUESTION' if questions else 'ESSAY',
        llm_prompt_budget=(
            {'mode': 'PER_QUESTION', 'limit': None, 'question_limits': questions}
            if questions
            else {**default_prompt_budget(), 'limit': limit}
        ),
    )


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/budgets.db')
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with factory() as session:
                yield session

    monkeypatch.setattr(budgets, 'get_async_db_context', context)
    monkeypatch.setattr(budgets.BudgetRun, 'notify', AsyncMock())

    async def run_test(body):
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: Bucket.metadata.create_all(sync, tables=[Bucket.__table__, Reservation.__table__])
            )
        try:
            await body(factory)
        finally:
            await engine.dispose()

    return lambda body: asyncio.run(run_test(body))


def test_validation_and_remapping():
    assert LLMPromptBudget().limit == 100
    for value in [-1, 1.5, True, '2', 2147483648]:
        with pytest.raises(ValidationError):
            LLMPromptBudget(limit=value)
    assert LLMPromptBudget(limit=0).limit == 0
    with pytest.raises(ValidationError):
        LLMPromptBudget(mode='PER_QUESTION', question_limits={'q': 1})
    with pytest.raises(ValidationError):
        PlanItemForm(
            task_type='ESSAY',
            title='Essay',
            essay_topic_mode='SPECIFIC',
            essay_topic_id='topic',
            llm_prompt_budget={'mode': 'PER_QUESTION', 'limit': None, 'question_limits': {'q': 1}},
        )
    assert PlanItemForm(task_type='SURVEY', title='Survey', survey_task_id='s').llm_prompt_budget is None
    assert remap_prompt_budget({'mode': 'PER_QUESTION', 'limit': None, 'question_limits': {'q': 7}}, {'q': 'copy'})[
        'question_limits'
    ] == {'copy': 7}
    with pytest.raises(ValueError):
        remap_prompt_budget({'mode': 'PER_QUESTION', 'limit': None, 'question_limits': {'q': 7}}, {})


def test_atomic_final_allowance_and_idempotent_completion(database):
    async def body(factory):
        current = task(limit=1)
        results = await asyncio.gather(*(budgets.reserve(current) for _ in range(8)), return_exceptions=True)
        accepted = [result for result in results if isinstance(result, str)]
        rejected = [result for result in results if isinstance(result, HTTPException)]
        assert len(accepted) == 1 and len(rejected) == 7, results
        assert all(result.detail['code'] == 'EXPERIMENT_PROMPT_LIMIT_REACHED' for result in rejected)
        await asyncio.gather(budgets.settle(accepted[0], True), budgets.settle(accepted[0], True))
        assert (await budgets.task_usage(current))['scopes']['TASK'] == budgets.usage_value(1, 1, 0)
        with pytest.raises(HTTPException):
            await budgets.reserve(current)

    database(body)


def test_independent_questions_sessions_zero_and_refunds(database):
    async def body(factory):
        current = task(questions={'q1': 0, 'q2': 1, 'q3': 2})
        for question in (None, 'foreign'):
            with pytest.raises(HTTPException) as error:
                await budgets.reserve(current, question)
            assert error.value.status_code == 422
        with pytest.raises(HTTPException):
            await budgets.reserve(current, 'q1')
        first = await budgets.reserve(current, 'q2')
        third = await budgets.reserve(current, 'q3')
        other = await budgets.reserve(task('other', limit=1))
        await budgets.settle(first, False)
        retry = await budgets.reserve(current, 'q2')
        await budgets.settle(retry, True)
        await budgets.settle(third, True)
        await budgets.settle(other, True)
        usage = await budgets.task_usage(current)
        assert usage['scopes']['q2']['used'] == 1
        assert usage['scopes']['q3']['available'] == 1

    database(body)


def test_expired_reservation_reclaimed_and_cannot_commit(database):
    async def body(factory):
        current = task(limit=1)
        stale = await budgets.reserve(current)
        assert await budgets.renew(stale)
        async with factory() as db:
            await db.execute(update(Reservation).where(Reservation.id == stale).values(lease_expires_at=0))
            await db.commit()
        assert not await budgets.renew(stale)
        assert (await budgets.task_usage(current))['scopes']['TASK']['available'] == 1
        fresh = await budgets.reserve(current)
        assert not await budgets.settle(stale, True)
        await budgets.settle(fresh, True)
        assert (await budgets.task_usage(current))['scopes']['TASK']['used'] == 1

    database(body)


@pytest.mark.parametrize(
    'ending,success',
    [
        ('data: [DONE]\n\n', True),
        ('data: {"choices":[{"finish_reason":"stop"}]}\n\n', True),
        ('data: {"type":"response.completed"}\n\n', True),
        ('data: {"type":"response.incomplete"}\n\n', True),
        ('data: {"type":"response.failed"}\n\n', False),
        ('', False),
        ('data: {"error":{"message":"failed"}}\n\n', False),
    ],
)
def test_stream_completion_or_refund(database, ending, success):
    async def body(factory):
        current = task(limit=1)
        run = budgets.BudgetRun(current, 'user')
        await run.start()

        async def chunks():
            payload = ('data: {"choices":[{"delta":{"content":"héllo"}}]}\n\n' + ending).encode()
            for byte in payload:
                yield bytes([byte])

        response = run.wrap_delivery(run.observe(StreamingResponse(chunks())))
        if success:
            async for _ in response.body_iterator:
                pass
        else:
            with pytest.raises(RuntimeError):
                async for _ in response.body_iterator:
                    pass
        assert (await budgets.task_usage(current))['scopes']['TASK']['used'] == int(success)
        assert (await budgets.task_usage(current))['scopes']['TASK']['pending'] == 0

    database(body)


def test_cancelled_partial_stream_refunds(database):
    async def body(factory):
        current = task(limit=1)
        run = budgets.BudgetRun(current, 'user')
        await run.start()

        async def chunks():
            yield 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise asyncio.CancelledError()

        response = run.wrap_delivery(run.observe(StreamingResponse(chunks())))
        with pytest.raises(asyncio.CancelledError):
            async for _ in response.body_iterator:
                pass
        assert (await budgets.task_usage(current))['scopes']['TASK']['available'] == 1

    database(body)


@pytest.mark.parametrize(
    'response,success',
    [
        ({'choices': [{'message': {'content': 'ok'}}]}, True),
        (JSONResponse({'error': 'bad'}, status_code=400), False),
        ({'error': 'bad'}, False),
    ],
)
def test_non_streaming_response(database, response, success):
    async def body(factory):
        current = task(limit=1)
        run = budgets.BudgetRun(current, 'user')
        await run.start()
        run.observe(response)
        await run.finish(True)
        assert (await budgets.task_usage(current))['scopes']['TASK']['used'] == int(success)

    database(body)


def test_provider_http_guard(monkeypatch):
    from open_webui.utils.experiments import require_guarded_experiment_generation, Experiments, ExperimentState

    monkeypatch.setattr(Experiments, 'get_current', AsyncMock(return_value=(ExperimentState.IN_PROGRESS, None, None)))
    for path in ('/openai/chat/completions', '/ollama/api/generate/0', '/ollama/api/chat', '/ollama/v1/responses'):
        request = Request({'type': 'http', 'method': 'POST', 'path': path, 'headers': []})
        with pytest.raises(HTTPException):
            asyncio.run(require_guarded_experiment_generation(request, SimpleNamespace(id='student')))
