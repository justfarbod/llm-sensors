"""Atomic prompt reservations, independent of perturbation request numbering."""

import asyncio
import codecs
import json
import logging
import time
import uuid
from contextlib import suppress

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse

from open_webui.internal.db import get_async_db_context
from open_webui.models.experiment_prompt_budgets import (
    ExperimentPromptBucket as Bucket,
    ExperimentPromptReservation as Reservation,
    LLMPromptBudget,
)

log = logging.getLogger(__name__)
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30


def budget_config(task):
    raw = getattr(task, 'llm_prompt_budget', None)
    return raw if isinstance(raw, LLMPromptBudget) else LLMPromptBudget.model_validate(raw or {})


def resolve_scope(task, question_id=None):
    budget = budget_config(task)
    if budget.mode == 'TASK':
        return 'TASK', budget.limit
    if not question_id or question_id not in budget.question_limits:
        raise HTTPException(status_code=422, detail='Select a question belonging to this task before using chat.')
    return question_id, budget.question_limits[question_id]


def bucket_id(task_id, scope):
    return f'{task_id}:{scope}'


async def _ensure_bucket(task_id, scope):
    # Separate transaction: concurrent first requests may race to insert this row.
    async with get_async_db_context() as db:
        key = bucket_id(task_id, scope)
        if await db.get(Bucket, key) is None:
            db.add(Bucket(id=key, session_task_id=task_id, scope_key=scope, used=0, pending=0))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()


async def _locked_bucket(db, key):
    # A write, rather than SELECT FOR UPDATE alone, serializes SQLite as well as PostgreSQL.
    await db.execute(update(Bucket).where(Bucket.id == key).values(used=Bucket.used))
    row = await db.get(Bucket, key, populate_existing=True)
    expired = (
        await db.execute(
            update(Reservation)
            .where(
                Reservation.bucket_id == key,
                Reservation.status == 'PENDING',
                Reservation.lease_expires_at <= time.time_ns(),
            )
            .values(status='EXPIRED')
            .returning(Reservation.id)
        )
    ).all()
    row.pending -= len(expired)
    return row


def usage_value(limit, used=0, pending=0):
    return {
        'limit': limit,
        'used': used,
        'pending': pending,
        'remaining': max(0, limit - used),
        'available': max(0, limit - used - pending),
    }


async def task_usage(task, db=None):
    if task.task_type == 'SURVEY':
        return None
    budget = budget_config(task)
    async with get_async_db_context(db) as db:
        rows = (await db.execute(select(Bucket).where(Bucket.session_task_id == task.id))).scalars().all()
        # Exclude expired leases even before the next reservation reclaims them.
        pending = (
            (
                await db.execute(
                    select(Reservation.bucket_id).where(
                        Reservation.bucket_id.in_([row.id for row in rows]),
                        Reservation.status == 'PENDING',
                        Reservation.lease_expires_at > time.time_ns(),
                    )
                )
            )
            .scalars()
            .all()
            if rows
            else []
        )
        counts = {row.scope_key: (row.used, pending.count(row.id)) for row in rows}
    limits = {'TASK': budget.limit} if budget.mode == 'TASK' else budget.question_limits
    return {
        'mode': budget.mode,
        'scopes': {key: usage_value(limit, *counts.get(key, (0, 0))) for key, limit in limits.items()},
    }


async def reserve(task, question_id=None):
    scope, limit = resolve_scope(task, question_id)
    await _ensure_bucket(task.id, scope)
    async with get_async_db_context() as db:
        row = await _locked_bucket(db, bucket_id(task.id, scope))
        if row.used + row.pending >= limit:
            detail = {
                'code': 'EXPERIMENT_PROMPT_LIMIT_REACHED',
                'message': 'No prompts are available for this task or question.',
                'task_id': task.id,
                'scope': scope,
                **usage_value(limit, row.used, row.pending),
            }
            await db.commit()  # Persist expired-reservation cleanup even when exhausted.
            raise HTTPException(status_code=429, detail=detail)
        now = time.time_ns()
        reservation_id = str(uuid.uuid4())
        row.pending += 1
        db.add(
            Reservation(
                id=reservation_id,
                bucket_id=row.id,
                status='PENDING',
                lease_expires_at=now + LEASE_SECONDS * 10**9,
                created_at=now,
            )
        )
        await db.commit()
        return reservation_id


async def renew(reservation_id):
    async with get_async_db_context() as db:
        now = time.time_ns()
        result = await db.execute(
            update(Reservation)
            .where(
                Reservation.id == reservation_id,
                Reservation.status == 'PENDING',
                Reservation.lease_expires_at > now,
            )
            .values(lease_expires_at=now + LEASE_SECONDS * 10**9)
        )
        await db.commit()
        return bool(result.rowcount)


async def settle(reservation_id, success):
    async with get_async_db_context() as db:
        reservation = await db.get(Reservation, reservation_id)
        if not reservation:
            return False
        row = await _locked_bucket(db, reservation.bucket_id)
        await db.refresh(reservation)
        if reservation.status != 'PENDING':
            await db.commit()
            return False
        reservation.status = 'COMPLETED' if success else 'RELEASED'
        row.pending -= 1
        if success:
            row.used += 1
        await db.commit()
        return True


class CompletionObserver:
    """Observe complete SSE events, including chunks split at arbitrary byte boundaries."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.buffer = ''
        self.completed = False
        self.failed = False

    def feed(self, chunk):
        self.buffer += self.decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        self.buffer = self.buffer.replace('\r\n', '\n')
        while '\n\n' in self.buffer:
            event, self.buffer = self.buffer.split('\n\n', 1)
            data = '\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
            if data == '[DONE]':
                self.completed = True
            elif data:
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                if isinstance(payload, dict):
                    if payload.get('error') or payload.get('type') in (
                        'error',
                        'response.failed',
                        'response.cancelled',
                    ):
                        self.failed = True
                    if (
                        payload.get('done') is True
                        or payload.get('type') in ('response.completed', 'response.incomplete', 'message_stop')
                        or any(
                            choice.get('finish_reason') is not None
                            for choice in (payload.get('choices') or [])
                            if isinstance(choice, dict)
                        )
                    ):
                        self.completed = True


class BudgetRun:
    def __init__(self, task, user_id, question_id=None):
        self.task = task
        self.user_id = user_id
        self.question_id = question_id
        self.reservation_id = None
        self.heartbeat = None
        self.owner = None
        self.observer = CompletionObserver()
        self.finished = False

    async def notify(self):
        try:
            from open_webui.socket.main import sio

            await sio.emit(
                'experiment:prompt-budget',
                {'task_id': self.task.id, 'usage': await task_usage(self.task)},
                room=f'user:{self.user_id}',
            )
        except Exception:
            log.debug('Unable to broadcast prompt usage', exc_info=True)

    async def start(self):
        self.reservation_id = await reserve(self.task, self.question_id)
        self.owner = asyncio.current_task()
        self.heartbeat = asyncio.create_task(self._heartbeat())
        await self.notify()

    async def _heartbeat(self):
        deadline = time.monotonic() + LEASE_SECONDS
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if self.owner and self.owner.done():
                break
            try:
                if not await renew(self.reservation_id):
                    break
                deadline = time.monotonic() + LEASE_SECONDS
            except Exception:
                log.exception('Unable to renew prompt reservation')
                if time.monotonic() < deadline:
                    continue
                break
        if self.owner and not self.owner.done():
            self.owner.cancel()

    def observe(self, response):
        if getattr(response, '_experiment_budget_observer', None) is self:
            return response
        self.observer = CompletionObserver()
        if not isinstance(response, StreamingResponse):
            data = response
            if hasattr(response, 'body'):
                try:
                    data = json.loads(response.body)
                except ValueError:
                    data = None
            self.observer.failed = (
                getattr(response, 'status_code', 200) >= 400 or not isinstance(data, dict) or bool(data.get('error'))
            )
            self.observer.completed = not self.observer.failed
            return response
        response._experiment_budget_observer = self
        observer = self.observer
        original = response.body_iterator

        async def stream():
            try:
                async for chunk in original:
                    observer.feed(chunk)
                    if observer.failed:
                        raise RuntimeError('Provider returned a streaming error.')
                    yield chunk
                if not observer.completed:
                    raise RuntimeError('Provider stream ended before completion.')
            finally:
                if hasattr(original, 'aclose'):
                    await original.aclose()

        response.body_iterator = stream()
        return response

    async def finish(self, success):
        if self.finished or not self.reservation_id:
            return
        self.finished = True
        if self.heartbeat:
            self.heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat
        await settle(self.reservation_id, success and self.observer.completed and not self.observer.failed)
        await self.notify()

    def wrap_delivery(self, response):
        """HTTP streaming is consumed after the route returns; settle after delivery."""
        original = response.body_iterator

        async def stream():
            self.owner = asyncio.current_task()
            success = False
            try:
                async for chunk in original:
                    yield chunk
                success = True
            finally:
                try:
                    if hasattr(original, 'aclose'):
                        await original.aclose()
                finally:
                    await asyncio.shield(self.finish(success))

        response.body_iterator = stream()
        return response
