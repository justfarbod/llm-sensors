from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio

import pytest
from pydantic import ValidationError

from open_webui.models.experiment_perturbations import (
    ActivationMode,
    ExperimentConditionForm,
    PromptInjectionForm,
    ResponseTimingForm,
)
from open_webui.models.experiment_plans import ExperimentPlanForm
from open_webui.utils.experiment_perturbations import (
    PerturbationRequestContext,
    _cadence_matches,
    apply_request_injections,
    apply_nonstream_timing,
    assign_condition,
    wrap_streaming_response,
)
from starlette.responses import JSONResponse, StreamingResponse


def essay_item():
    return {
        'id': 'item',
        'task_type': 'ESSAY',
        'title': 'Essay',
        'essay_topic_mode': 'SPECIFIC',
        'essay_topic_id': 'topic',
    }


def test_plan_defaults_to_a_normal_control_condition():
    form = ExperimentPlanForm.model_validate({'items': [essay_item()]})
    assert len(form.conditions) == 1
    assert form.conditions[0].is_control
    assert form.conditions[0].allocation_percent == 100
    assert form.conditions[0].response_timing.mode == 'NORMAL'


def test_control_and_allocation_validation():
    with pytest.raises(ValidationError):
        ExperimentPlanForm.model_validate(
            {
                'items': [essay_item()],
                'conditions': [
                    {
                        'name': 'Invalid control',
                        'allocation_percent': 100,
                        'is_control': True,
                        'prompt_injection': {'enabled': True, 'instruction': 'hidden'},
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        ExperimentPlanForm.model_validate(
            {
                'items': [essay_item()],
                'conditions': [
                    {'name': 'Control', 'allocation_percent': 50, 'is_control': True},
                    {'name': 'Treatment', 'allocation_percent': 40},
                ],
            }
        )


def test_timing_and_activation_validation():
    with pytest.raises(ValidationError):
        ResponseTimingForm(mode='DELAYED')
    with pytest.raises(ValidationError):
        PromptInjectionForm(enabled=True, instruction='')
    normal = ResponseTimingForm(
        mode='NORMAL',
        delay_seconds=1,
        target_duration_seconds=5,
        rate_value=100,
    )
    assert normal.delay_seconds is None
    assert normal.target_duration_seconds is None
    assert normal.rate_value is None
    rule = ExperimentConditionForm.model_validate(
        {
            'name': 'Treatment',
            'allocation_percent': 100,
            'prompt_injection': {
                'enabled': True,
                'instruction': 'bias',
                'activation': {'mode': 'PROMPT_RANGE', 'range_start': 2, 'range_end': 4},
            },
        }
    ).prompt_injection.activation
    assert _cadence_matches(rule, request_sequence=9, prompt_number=2)
    assert not _cadence_matches(rule, request_sequence=9, prompt_number=5)
    rule.mode = ActivationMode.EVERY_N_PROMPTS
    rule.count = 3
    assert _cadence_matches(rule, 5, 6)


def test_condition_assignment_is_stable_and_weighted():
    plan = SimpleNamespace(
        id='plan',
        conditions=[
            SimpleNamespace(id='control', enabled=True, allocation_percent=30),
            SimpleNamespace(id='treatment', enabled=True, allocation_percent=70),
        ],
    )
    first = assign_condition(plan, 'participant')
    second = assign_condition(plan, 'participant')
    assert first == second
    assert first[0] in {'control', 'treatment'}
    assert 0 <= first[2] < 1


def test_request_only_injections_preserve_genuine_message():
    condition = ExperimentConditionForm.model_validate(
        {
            'name': 'Treatment',
            'allocation_percent': 100,
            'prompt_injection': {
                'enabled': True,
                'instruction': 'hidden instruction',
                'position': 'AFTER_PARTICIPANT',
            },
            'memory_injection': {'enabled': True, 'content': 'hidden memory'},
        }
    )
    context = PerturbationRequestContext('request', condition, True, True, condition.response_timing)
    original = [{'role': 'system', 'content': 'base'}, {'role': 'user', 'content': 'genuine'}]
    result = apply_request_injections(original, context)
    assert original == [{'role': 'system', 'content': 'base'}, {'role': 'user', 'content': 'genuine'}]
    assert result[1]['content'] == 'genuine'
    assert 'hidden memory' in result[0]['content']
    assert 'hidden instruction' in result[2]['content']


def test_delayed_stream_buffers_real_provider_content(monkeypatch):
    timing = ResponseTimingForm(mode='DELAYED', delay_seconds=1, reveal_style='FULL')
    context = PerturbationRequestContext('request', SimpleNamespace(), False, False, timing)
    updates = AsyncMock()
    sleeps = AsyncMock()
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.update_request', updates)
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.asyncio.sleep', sleeps)

    async def source():
        yield 'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n'
        yield 'data: [DONE]\n\n'

    response = StreamingResponse(source(), media_type='text/event-stream')

    async def collect():
        wrapped = await wrap_streaming_response(response, context)
        return [line async for line in wrapped.body_iterator]

    emitted = asyncio.run(collect())
    assert 'answer' in ''.join(emitted)
    assert sleeps.await_count == 1
    assert any(call.kwargs.get('buffered_output') == {'content': 'answer'} for call in updates.await_args_list)


def test_provider_stream_error_bypasses_artificial_delay(monkeypatch):
    timing = ResponseTimingForm(mode='DELAYED', delay_seconds=300, reveal_style='FULL')
    context = PerturbationRequestContext('request', SimpleNamespace(), False, False, timing)
    updates = AsyncMock()
    sleeps = AsyncMock()
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.update_request', updates)
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.asyncio.sleep', sleeps)

    async def source():
        yield 'data: {"error":{"message":"provider failed"}}\n\n'

    response = StreamingResponse(source(), media_type='text/event-stream')

    async def collect():
        wrapped = await wrap_streaming_response(response, context)
        return [line async for line in wrapped.body_iterator]

    emitted = asyncio.run(collect())
    assert 'provider failed' in ''.join(emitted)
    sleeps.assert_not_awaited()
    assert any(call.kwargs.get('status') == 'FAILED' for call in updates.await_args_list)


def test_slow_stream_drains_buffer_before_terminal_event(monkeypatch):
    timing = ResponseTimingForm(mode='SLOW', target_duration_seconds=2, maximum_chunk_size=2)
    context = PerturbationRequestContext('request', SimpleNamespace(), False, False, timing)
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.update_request', AsyncMock())
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.asyncio.sleep', AsyncMock())

    async def source():
        yield 'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n'
        yield 'data: [DONE]\n\n'

    async def collect():
        response = StreamingResponse(source(), media_type='text/event-stream')
        wrapped = await wrap_streaming_response(response, context)
        return [line async for line in wrapped.body_iterator]

    emitted = asyncio.run(collect())
    assert emitted[-1] == 'data: [DONE]\n\n'
    assert ''.join(_line for _line in emitted[:-1]).count('content') == 3


def test_nonstream_fast_response_becomes_paced_stream(monkeypatch):
    timing = ResponseTimingForm(
        mode='FAST',
        rate_value=100,
        rate_unit='CHARACTERS_PER_SECOND',
        maximum_chunk_size=3,
    )
    context = PerturbationRequestContext('request', SimpleNamespace(), False, False, timing)
    updates = AsyncMock()
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.update_request', updates)
    monkeypatch.setattr('open_webui.utils.experiment_perturbations.asyncio.sleep', AsyncMock())
    response = JSONResponse({'choices': [{'message': {'role': 'assistant', 'content': 'answer'}}]})

    async def collect():
        wrapped = await apply_nonstream_timing(response, context)
        assert isinstance(wrapped, StreamingResponse)
        return [line async for line in wrapped.body_iterator]

    emitted = asyncio.run(collect())
    assert emitted[-1] == 'data: [DONE]\n\n'
    assert any('ans' in line for line in emitted)
    assert any(call.kwargs.get('status') == 'COMPLETED' for call in updates.await_args_list)
