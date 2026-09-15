import asyncio
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import get_async_db_context
from open_webui.models.experiment_perturbations import (
    ActivationMode,
    ExperimentLLMRequest,
    ResponseTimingMode,
)
from open_webui.utils.misc import add_or_update_system_message


def _hmac_value(*parts: str) -> tuple[float, str]:
    digest = hmac.new(str(WEBUI_SECRET_KEY).encode(), ':'.join(parts).encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], 'big') / float(2**64), digest.hex()[:24]


def assign_condition(plan, user_id: str):
    enabled = [
        condition for condition in (plan.conditions or [])
        if condition.enabled and condition.allocation_percent > 0
    ]
    if not enabled:
        return None, None, None
    draw, assignment_id = _hmac_value('condition', plan.id, user_id)
    point = draw * 100
    cumulative = 0
    selected = enabled[-1]
    for condition in enabled:
        cumulative += condition.allocation_percent
        if point < cumulative:
            selected = condition
            break
    return selected.id, assignment_id, draw


def _cadence_matches(rule, request_sequence: int, prompt_number: int) -> bool:
    if rule.mode == ActivationMode.EVERY_REQUEST:
        return True
    if rule.mode == ActivationMode.FIRST_REQUEST:
        return request_sequence == 1
    if rule.mode == ActivationMode.AFTER_PROMPT_COUNT:
        return prompt_number > (rule.count or 0)
    if rule.mode == ActivationMode.EVERY_N_PROMPTS:
        return bool(rule.count and prompt_number % rule.count == 0)
    if rule.mode == ActivationMode.PROMPT_RANGE:
        return bool(rule.range_start and rule.range_end and rule.range_start <= prompt_number <= rule.range_end)
    return False


def _scope_matches(rule, plan_item_id: Optional[str]) -> bool:
    return rule.scope.value == 'ALL_TASKS' or bool(plan_item_id and plan_item_id in rule.plan_item_ids)


@dataclass
class PerturbationRequestContext:
    request_id: str
    condition: Any
    prompt_active: bool
    timing: Any


async def prepare_request(experiment_session, plan, session_task, metadata) -> Optional[PerturbationRequestContext]:
    if not experiment_session or not plan or not experiment_session.condition_id:
        return None
    condition = next(
        (candidate for candidate in plan.conditions if candidate.id == experiment_session.condition_id), None
    )
    if condition is None or not condition.enabled:
        return None

    user_message_id = metadata.get('user_message_id')
    for _ in range(3):
        async with get_async_db_context() as db:
            request_sequence = (
                await db.execute(
                    select(func.max(ExperimentLLMRequest.request_sequence)).where(
                        ExperimentLLMRequest.experiment_session_id == experiment_session.id
                    )
                )
            ).scalar() or 0
            request_sequence += 1
            previous_prompt = None
            if user_message_id:
                previous_prompt = (
                    await db.execute(
                        select(ExperimentLLMRequest.prompt_number)
                        .where(
                            ExperimentLLMRequest.experiment_session_id == experiment_session.id,
                            ExperimentLLMRequest.user_message_id == user_message_id,
                        )
                        .limit(1)
                    )
                ).scalar()
            prompt_number = previous_prompt or (
                (
                    await db.execute(
                        select(func.max(ExperimentLLMRequest.prompt_number)).where(
                            ExperimentLLMRequest.experiment_session_id == experiment_session.id
                        )
                    )
                ).scalar()
                or 0
            ) + (0 if previous_prompt else 1)

            plan_item_id = getattr(session_task, 'plan_item_id', None)
            prompt_rule = condition.prompt_injection.activation
            prompt_draw, prompt_randomization_id = _hmac_value(
                'activation', plan.id, experiment_session.id, str(request_sequence), 'prompt'
            )
            prompt_active = bool(
                condition.prompt_injection.enabled
                and _scope_matches(prompt_rule, plan_item_id)
                and _cadence_matches(prompt_rule, request_sequence, prompt_number)
                and prompt_draw < prompt_rule.probability
            )
            timing = condition.response_timing
            now = time.time_ns()
            request_row = ExperimentLLMRequest(
                id=str(uuid.uuid4()),
                experiment_session_id=experiment_session.id,
                condition_id=condition.id,
                plan_id=plan.id,
                plan_version=plan.version,
                session_task_id=getattr(session_task, 'id', None),
                chat_id=metadata.get('chat_id'),
                user_message_id=user_message_id,
                assistant_message_id=metadata.get('message_id'),
                request_sequence=request_sequence,
                prompt_number=prompt_number,
                assignment_identifier=experiment_session.condition_assignment_id,
                prompt_active=prompt_active,
                prompt_draw=prompt_draw,
                prompt_randomization_id=prompt_randomization_id,
                timing_mode=timing.mode.value,
                timing_parameters=timing.model_dump(mode='json'),
                request_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(request_row)
            try:
                await db.commit()
                return PerturbationRequestContext(
                    request_id=request_row.id,
                    condition=condition,
                    prompt_active=prompt_active,
                    timing=timing,
                )
            except IntegrityError:
                await db.rollback()
    raise RuntimeError('Unable to reserve an experiment request sequence.')


def apply_request_injections(messages: list[dict], context: Optional[PerturbationRequestContext]) -> list[dict]:
    if context is None or not context.prompt_active:
        return messages
    result = [dict(message) for message in messages]
    prompt = context.condition.prompt_injection
    wrapped = f'<experiment_instruction>\n{prompt.instruction}\n</experiment_instruction>'
    if prompt.position.value == 'SYSTEM':
        result = add_or_update_system_message(wrapped, result, append=True)
    else:
        last_user = next(
            (index for index in range(len(result) - 1, -1, -1) if result[index].get('role') == 'user'), None
        )
        if last_user is not None:
            insertion_index = last_user if prompt.position.value == 'BEFORE_PARTICIPANT' else last_user + 1
            result.insert(insertion_index, {'role': 'user', 'content': wrapped})
    return result


async def update_request(request_id: Optional[str], **values):
    if not request_id:
        return
    async with get_async_db_context() as db:
        row = await db.get(ExperimentLLMRequest, request_id)
        if row:
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = time.time_ns()
            await db.commit()


def _extract_text(line: str) -> str:
    if not line.startswith('data:'):
        return ''
    raw = line[5:].strip()
    if not raw or raw == '[DONE]':
        return ''
    try:
        data = json.loads(raw)
    except Exception:
        return ''
    choices = data.get('choices') or []
    if choices:
        content = choices[0].get('delta', {}).get('content') or choices[0].get('message', {}).get('content') or ''
        return content if isinstance(content, str) else ''
    if data.get('type') == 'response.output_text.delta':
        delta = data.get('delta') or ''
        return delta if isinstance(delta, str) else ''
    return ''


def _contains_provider_error(line: str) -> bool:
    if not line.startswith('data:'):
        return False
    raw = line[5:].strip()
    if not raw or raw == '[DONE]':
        return False
    try:
        data = json.loads(raw)
    except Exception:
        return False
    return bool(data.get('error') or data.get('type') == 'response.failed')


def _is_terminal_or_full_output_event(line: str) -> bool:
    if not line.startswith('data:'):
        return False
    raw = line[5:].strip()
    if raw == '[DONE]':
        return True
    try:
        event_type = json.loads(raw).get('type')
    except Exception:
        return False
    return event_type in {
        'response.content_part.done',
        'response.output_text.done',
        'response.output_item.done',
        'response.completed',
    }


def _split_sse_content(line: str, chunk_size: int, unit: str = 'CHARACTER'):
    if not line.startswith('data:'):
        return [line]
    raw = line[5:].strip()
    try:
        data = json.loads(raw)
        choices = data.get('choices') or []
        content = choices[0].get('delta', {}).get('content') if choices else None
        response_delta = data.get('delta') if data.get('type') == 'response.output_text.delta' else None
        content = content if isinstance(content, str) else response_delta
        if not isinstance(content, str) or unit == 'CHUNK':
            return [line]
        if unit == 'WORD':
            words = re.findall(r'\S+\s*', content)
            text_chunks = [''.join(words[start : start + chunk_size]) for start in range(0, len(words), chunk_size)]
        else:
            text_chunks = [content[start : start + chunk_size] for start in range(0, len(content), chunk_size)]
        if len(text_chunks) <= 1:
            return [line]
        chunks = []
        for text_chunk in text_chunks:
            clone = json.loads(json.dumps(data))
            if choices:
                clone['choices'][0]['delta']['content'] = text_chunk
            else:
                clone['delta'] = text_chunk
            chunks.append(f'data: {json.dumps(clone)}\n\n')
        return chunks
    except Exception:
        return [line]


def _replace_sse_content(line: str, content: str) -> str:
    raw = line[5:].strip()
    data = json.loads(raw)
    choices = data.get('choices') or []
    if choices:
        data['choices'][0]['delta']['content'] = content
    else:
        data['delta'] = content
    return f'data: {json.dumps(data)}\n\n'


def _deterministic_chunk_size(seed: str, index: int, minimum: int, maximum: int) -> int:
    minimum = max(1, minimum)
    maximum = max(minimum, maximum)
    if minimum == maximum:
        return minimum
    digest = hashlib.sha256(f'{seed}:{index}'.encode()).digest()
    return minimum + int.from_bytes(digest[:4], 'big') % (maximum - minimum + 1)


def _bounded_text_chunks(text: str, seed: str, unit: str, minimum: int, maximum: int) -> list[str]:
    if not text:
        return []
    values = re.findall(r'\S+\s*', text) if unit == 'WORD' else list(text)
    chunks = []
    cursor = 0
    index = 0
    while cursor < len(values):
        size = _deterministic_chunk_size(seed, index, minimum, maximum)
        chunks.append(''.join(values[cursor : cursor + size]))
        cursor += size
        index += 1
    if len(chunks) == 1 and len(chunks[0]) > 1:
        midpoint = max(1, len(chunks[0]) // 2)
        chunks = [chunks[0][:midpoint], chunks[0][midpoint:]]
    return chunks


class _BoundedSSEChunker:
    def __init__(self, seed: str, unit: str, minimum: int, maximum: int):
        self.seed = seed
        self.unit = unit
        self.minimum = minimum
        self.maximum = maximum
        self.index = 0
        self.template = None
        self.pending_text = ''
        self.pending_events: list[str] = []

    def _target(self) -> int:
        return _deterministic_chunk_size(
            self.seed,
            self.index,
            self.minimum,
            self.maximum,
        )

    def feed(self, line: str) -> list[str]:
        text = _extract_text(line)
        if not text:
            return []
        if self.template is None:
            self.template = line
        if self.unit == 'CHUNK':
            self.pending_events.append(text)
        else:
            self.pending_text += text
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[str]:
        chunks = []
        while self.template is not None:
            target = self._target()
            if self.unit == 'CHUNK':
                if len(self.pending_events) < target and not final:
                    break
                if not self.pending_events:
                    break
                take = min(target, len(self.pending_events))
                content = ''.join(self.pending_events[:take])
                del self.pending_events[:take]
            elif self.unit == 'WORD':
                words = re.findall(r'\S+\s*', self.pending_text)
                complete = len(words) if final or self.pending_text[-1:].isspace() else max(0, len(words) - 1)
                if complete < target and not final:
                    break
                if not words:
                    break
                take = min(target, len(words))
                content = ''.join(words[:take])
                self.pending_text = self.pending_text[len(content) :]
            else:
                if len(self.pending_text) < target and not final:
                    break
                if not self.pending_text:
                    break
                take = min(target, len(self.pending_text))
                content = self.pending_text[:take]
                self.pending_text = self.pending_text[take:]
            chunks.append(_replace_sse_content(self.template, content))
            self.index += 1
        return chunks


async def wrap_streaming_response(response, context: Optional[PerturbationRequestContext]):
    if context is None or not isinstance(response, StreamingResponse):
        return response
    timing = context.timing
    request_id = context.request_id
    await update_request(request_id, provider_started_at=time.time_ns())
    original = response.body_iterator

    async def generator():
        buffered = []
        slow_buffer = []
        terminal_buffer = []
        target_received_text = []
        first_received_monotonic = None
        first_emit_monotonic = None
        first_emit = False
        reveal_cursor = 0
        received_length = 0
        received_checkpoint = 0
        reveal_checkpoint = 0
        completed_normally = False
        target_chunker = _BoundedSSEChunker(
            request_id,
            timing.stream_unit.value if timing.mode == ResponseTimingMode.SLOW else 'CHARACTER',
            timing.minimum_chunk_size if timing.mode == ResponseTimingMode.SLOW else timing.maximum_chunk_size,
            timing.maximum_chunk_size,
        )
        try:
            async for raw_line in original:
                line = raw_line.decode('utf-8', 'replace') if isinstance(raw_line, bytes) else raw_line
                if _contains_provider_error(line):
                    await update_request(
                        request_id,
                        provider_completed_at=time.time_ns(),
                        status='FAILED',
                        error_type='ProviderStreamError',
                        streaming_completed_normally=False,
                    )
                    yield line
                    return
                text = _extract_text(line)
                if text and first_received_monotonic is None:
                    first_received_monotonic = time.monotonic()
                    await update_request(
                        request_id,
                        provider_first_token_at=time.time_ns(),
                        artificial_delay_started_at=(
                            time.time_ns() if timing.mode == ResponseTimingMode.DELAYED else None
                        ),
                    )
                if timing.mode == ResponseTimingMode.DELAYED:
                    if text:
                        buffered.append(line)
                    elif _is_terminal_or_full_output_event(line):
                        terminal_buffer.append(line)
                    else:
                        yield line
                    continue
                target_duration_mode = timing.mode == ResponseTimingMode.SLOW or (
                    timing.mode == ResponseTimingMode.FAST and timing.rate_unit.value == 'TARGET_DURATION'
                )
                if target_duration_mode and text:
                    pieces = target_chunker.feed(line)
                    target_received_text.append(text)
                    received_length += len(text)
                    slow_buffer.extend(pieces)
                    if received_length - received_checkpoint >= 200:
                        received_checkpoint = received_length
                        await update_request(
                            request_id,
                            buffered=True,
                            buffered_output={'content': ''.join(target_received_text)},
                            reveal_cursor=reveal_cursor,
                        )
                    continue
                if target_duration_mode and _is_terminal_or_full_output_event(line):
                    terminal_buffer.append(line)
                    continue
                chunk_size = (
                    timing.maximum_chunk_size
                    if timing.mode in {ResponseTimingMode.SLOW, ResponseTimingMode.FAST}
                    else 1000000
                )
                split_unit = (
                    'WORD'
                    if timing.mode == ResponseTimingMode.FAST and timing.rate_unit.value == 'WORDS_PER_SECOND'
                    else 'CHARACTER'
                )
                for emitted in _split_sse_content(line, chunk_size, split_unit):
                    emitted_text = _extract_text(emitted)
                    if emitted_text:
                        if not first_emit:
                            first_emit = True
                            await update_request(request_id, server_first_emit_at=time.time_ns())
                        elif timing.mode == ResponseTimingMode.FAST:
                            rate = timing.rate_value or 200
                            if timing.rate_unit.value == 'WORDS_PER_SECOND':
                                amount = max(1, len(re.findall(r'\S+', emitted_text)))
                            elif timing.rate_unit.value == 'TARGET_DURATION':
                                amount = len(emitted_text)
                                rate = max(1.0, 4000 / float(timing.target_duration_seconds or 1))
                            else:
                                amount = len(emitted_text)
                            await asyncio.sleep(amount / rate)
                    yield emitted
            completed_normally = True
            provider_completed = time.time_ns()
            await update_request(request_id, provider_completed_at=provider_completed)
            target_duration_mode = timing.mode == ResponseTimingMode.SLOW or (
                timing.mode == ResponseTimingMode.FAST and timing.rate_unit.value == 'TARGET_DURATION'
            )
            if target_duration_mode:
                slow_buffer.extend(target_chunker.flush())
                if len(slow_buffer) == 1:
                    only_text = _extract_text(slow_buffer[0])
                    if len(only_text) > 1:
                        midpoint = max(1, len(only_text) // 2)
                        slow_buffer = [
                            _replace_sse_content(slow_buffer[0], only_text[:midpoint]),
                            _replace_sse_content(slow_buffer[0], only_text[midpoint:]),
                        ]
                if slow_buffer:
                    first_emit = True
                    first_emit_monotonic = time.monotonic()
                    await update_request(request_id, server_first_emit_at=time.time_ns())
                    first_piece, *remaining_pieces = slow_buffer
                    yield first_piece
                    reveal_cursor += len(_extract_text(first_piece))
                    slow_buffer = remaining_pieces
                full_text = ''.join(target_received_text)
                if full_text:
                    await update_request(
                        request_id,
                        buffered=True,
                        buffered_output={'content': full_text},
                        reveal_cursor=reveal_cursor,
                    )
                started = first_emit_monotonic or time.monotonic()
                duration = float(timing.target_duration_seconds or 0)
                weights = []
                for emitted in slow_buffer:
                    emitted_text = _extract_text(emitted)
                    weight = max(1, len(emitted_text))
                    if (
                        timing.mode == ResponseTimingMode.SLOW
                        and timing.punctuation_pauses
                        and re.search(r'[.!?,;:]\s*$', emitted_text)
                    ):
                        weight *= 1.35
                    weights.append(weight)
                total_weight = sum(weights)
                cumulative_weight = 0.0
                for emitted in slow_buffer:
                    emitted_text = _extract_text(emitted)
                    cumulative_weight += weights.pop(0)
                    deadline = started + duration * (
                        cumulative_weight / max(total_weight, 1)
                    )
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                    yield emitted
                    reveal_cursor += len(emitted_text)
                    if reveal_cursor - reveal_checkpoint >= 200:
                        reveal_checkpoint = reveal_cursor
                        await update_request(request_id, reveal_cursor=reveal_cursor)
                if full_text:
                    await update_request(request_id, reveal_cursor=len(full_text))
                for emitted in terminal_buffer:
                    yield emitted
            if timing.mode == ResponseTimingMode.DELAYED:
                buffered_text = ''.join(_extract_text(line) for line in buffered)
                await update_request(
                    request_id,
                    buffered=True,
                    buffered_output={'content': buffered_text},
                    reveal_cursor=0,
                )
                if first_received_monotonic is not None:
                    remaining = float(timing.delay_seconds or 0) - (time.monotonic() - first_received_monotonic)
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                if first_received_monotonic is not None:
                    await update_request(request_id, artificial_delay_ended_at=time.time_ns())
                reveal_cursor = 0
                last_checkpoint = 0
                for line in buffered:
                    pieces = (
                        _split_sse_content(line, timing.maximum_chunk_size)
                        if timing.reveal_style.value == 'QUICK_STREAM'
                        else [line]
                    )
                    for emitted in pieces:
                        emitted_text = _extract_text(emitted)
                        if emitted_text and not first_emit:
                            first_emit = True
                            await update_request(request_id, server_first_emit_at=time.time_ns())
                        elif emitted_text and timing.reveal_style.value == 'QUICK_STREAM':
                            await asyncio.sleep(len(emitted_text) / 500)
                        yield emitted
                        reveal_cursor += len(emitted_text)
                        if reveal_cursor - last_checkpoint >= 200:
                            last_checkpoint = reveal_cursor
                            await update_request(request_id, reveal_cursor=reveal_cursor)
                await update_request(request_id, reveal_cursor=len(buffered_text))
                for emitted in terminal_buffer:
                    yield emitted
            await update_request(
                request_id,
                server_completed_emit_at=time.time_ns(),
                streaming_completed_normally=True,
                status='COMPLETED',
                buffered_output=None,
            )
        except asyncio.CancelledError:
            await update_request(request_id, status='CANCELLED', streaming_completed_normally=False)
            raise
        except Exception as error:
            await update_request(
                request_id,
                provider_completed_at=time.time_ns(),
                status='FAILED',
                error_type=type(error).__name__,
                streaming_completed_normally=False,
            )
            raise
        finally:
            if not completed_normally and hasattr(original, 'aclose'):
                await original.aclose()

    response.body_iterator = generator()
    return response


async def apply_nonstream_timing(response, context: Optional[PerturbationRequestContext]):
    if context is None or isinstance(response, StreamingResponse):
        return response
    now = time.time_ns()
    await update_request(context.request_id, provider_first_token_at=now, provider_completed_at=now)
    timing = context.timing
    response_data = response if isinstance(response, dict) else None
    if response_data is None and hasattr(response, 'body'):
        try:
            response_data = json.loads(response.body)
        except Exception:
            response_data = None
    if isinstance(response_data, dict) and response_data.get('error'):
        await update_request(
            context.request_id,
            status='FAILED',
            error_type='ProviderResponseError',
            streaming_completed_normally=False,
        )
        return response
    choices = response_data.get('choices') if isinstance(response_data, dict) else None
    choice = choices[0] if choices else None
    message = choice.get('message', {}) if isinstance(choice, dict) else {}
    content = message.get('content') if isinstance(message, dict) else None
    content = content if isinstance(content, str) else ''

    if timing.mode == ResponseTimingMode.NORMAL or not content:
        emitted = time.time_ns()
        await update_request(
            context.request_id,
            server_first_emit_at=(emitted if content else None),
            server_completed_emit_at=emitted,
            streaming_completed_normally=True,
            status='COMPLETED',
        )
        return response

    can_synthesize = set(message).issubset({'role', 'content', 'tool_calls'})
    quick_synthetic = timing.mode != ResponseTimingMode.DELAYED or timing.reveal_style.value == 'QUICK_STREAM'
    if not can_synthesize or not quick_synthetic:
        delay = (
            float(timing.delay_seconds or 0)
            if timing.mode == ResponseTimingMode.DELAYED
            else float(timing.target_duration_seconds or 0)
        )
        await update_request(
            context.request_id,
            artificial_delay_started_at=(time.time_ns() if timing.mode == ResponseTimingMode.DELAYED else None),
            buffered=True,
            buffered_output={'content': content},
        )
        if delay:
            await asyncio.sleep(delay)
        emitted = time.time_ns()
        await update_request(
            context.request_id,
            artificial_delay_ended_at=(emitted if timing.mode == ResponseTimingMode.DELAYED else None),
            server_first_emit_at=emitted,
            server_completed_emit_at=emitted,
            reveal_cursor=len(content),
            streaming_completed_normally=True,
            status='COMPLETED',
            buffered_output=None,
        )
        return response

    request_id = context.request_id
    chunks = _bounded_text_chunks(
        content,
        request_id,
        timing.stream_unit.value if timing.mode == ResponseTimingMode.SLOW else 'CHARACTER',
        timing.minimum_chunk_size if timing.mode == ResponseTimingMode.SLOW else timing.maximum_chunk_size,
        timing.maximum_chunk_size,
    ) or [content]
    received_monotonic = time.monotonic()

    async def synthetic_generator():
        reveal_cursor = 0
        try:
            await update_request(
                request_id,
                buffered=True,
                buffered_output={'content': content},
                reveal_cursor=0,
                artificial_delay_started_at=(time.time_ns() if timing.mode == ResponseTimingMode.DELAYED else None),
            )
            tool_calls = message.get('tool_calls')
            if tool_calls:
                yield f'data: {json.dumps({"choices": [{"delta": {"tool_calls": tool_calls}}]})}\n\n'
            if timing.mode == ResponseTimingMode.DELAYED:
                remaining = float(timing.delay_seconds or 0) - (time.monotonic() - received_monotonic)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                await update_request(request_id, artificial_delay_ended_at=time.time_ns())
            started = time.monotonic()
            target_duration_mode = timing.mode == ResponseTimingMode.SLOW or (
                timing.mode == ResponseTimingMode.FAST
                and timing.rate_unit.value == 'TARGET_DURATION'
            )
            interval_weights = []
            for chunk in chunks[1:]:
                if (
                    timing.mode == ResponseTimingMode.FAST
                    and timing.rate_unit.value == 'WORDS_PER_SECOND'
                ):
                    weight = max(1, len(re.findall(r'\S+', chunk)))
                else:
                    weight = max(1, len(chunk))
                if (
                    timing.mode == ResponseTimingMode.SLOW
                    and timing.punctuation_pauses
                    and re.search(r'[.!?,;:]\s*$', chunk)
                ):
                    weight *= 1.35
                interval_weights.append(weight)
            total_weight = sum(interval_weights)
            cumulative_weight = 0.0
            for index, chunk in enumerate(chunks):
                if index:
                    weight = interval_weights[index - 1]
                    cumulative_weight += weight
                    if target_duration_mode:
                        deadline = started + float(
                            timing.target_duration_seconds or 0
                        ) * (cumulative_weight / max(total_weight, 1))
                    else:
                        rate = (
                            500
                            if timing.mode == ResponseTimingMode.DELAYED
                            else float(timing.rate_value or 1)
                        )
                        deadline = started + cumulative_weight / rate
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                if index == 0:
                    await update_request(request_id, server_first_emit_at=time.time_ns())
                yield f'data: {json.dumps({"choices": [{"delta": {"content": chunk}}]})}\n\n'
                reveal_cursor += len(chunk)
                if reveal_cursor == len(content) or reveal_cursor % 200 < len(chunk):
                    await update_request(request_id, reveal_cursor=reveal_cursor)
            final_data = {
                'choices': [
                    {
                        'delta': {},
                        'finish_reason': choice.get('finish_reason'),
                    }
                ],
                **({'usage': response_data.get('usage')} if response_data.get('usage') else {}),
                **(
                    {'selected_model_id': response_data.get('selected_model_id')}
                    if response_data.get('selected_model_id')
                    else {}
                ),
            }
            yield f'data: {json.dumps(final_data)}\n\n'
            yield 'data: [DONE]\n\n'
            await update_request(
                request_id,
                server_completed_emit_at=time.time_ns(),
                streaming_completed_normally=True,
                status='COMPLETED',
                buffered_output=None,
            )
        except asyncio.CancelledError:
            await update_request(request_id, status='CANCELLED', streaming_completed_normally=False)
            raise
        except Exception as error:
            await update_request(
                request_id,
                status='FAILED',
                error_type=type(error).__name__,
                streaming_completed_normally=False,
            )
            raise

    return StreamingResponse(
        synthetic_generator(),
        media_type='text/event-stream',
        background=getattr(response, 'background', None),
    )


def public_request_context(context: Optional[PerturbationRequestContext]):
    return {'request_id': context.request_id} if context else None
