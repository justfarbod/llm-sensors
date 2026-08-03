import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.config import EXPERIMENT_AGREEMENT_TEXT
from open_webui.internal.db import get_async_session
from open_webui.models.experiment_plans import (
    ExperimentPlans,
    ExperimentSessionTask,
    ExperimentTaskType,
    ProgressionMode,
    SessionTaskStatus,
)
from open_webui.models.experiments import (
    ExperimentSession,
    ExperimentState,
    Experiments,
    PostSurveyForm,
    PreSurveyForm,
    TaskNeutralPostSurveyForm,
    TaskNeutralPreSurveyForm,
)
from open_webui.models.question_submissions import (
    QuestionDraftForm,
    QuestionSubmission,
    QuestionSubmissions,
)
from open_webui.models.question_tasks import QuestionTasks
from open_webui.models.survey_submissions import SurveyDraftForm, SurveySubmissions
from open_webui.models.survey_tasks import SurveyTasks
from open_webui.tasks import create_task
from open_webui.utils.auth import get_verified_user
from open_webui.utils.question_grading import grade_response

router = APIRouter()


class ExperimentTopicResponse(BaseModel):
    id: str
    title: str
    question: str


class ExperimentTaskSummary(BaseModel):
    id: str
    position: int
    task_type: ExperimentTaskType
    title: str
    status: SessionTaskStatus
    question_count: Optional[int] = None
    essay_topic: Optional[ExperimentTopicResponse] = None
    survey_required: Optional[bool] = None


class ExperimentCurrentResponse(BaseModel):
    state: ExperimentState
    session_id: Optional[str] = None
    group_id: Optional[str] = None
    plan_id: Optional[str] = None
    progression_mode: Optional[ProgressionMode] = None
    chat_mode: Optional[str] = None
    survey_variant: Optional[str] = None
    tasks: list[ExperimentTaskSummary] = []
    topic: Optional[ExperimentTopicResponse] = None
    agreement_text: Optional[str] = None
    error: Optional[str] = None


class EssayDraftForm(BaseModel):
    content: str = Field(default='', max_length=500000)


async def response_for(user, db: AsyncSession):
    state, session, error = await Experiments.get_current(user, db=db)
    plan = await ExperimentPlans.get_plan(session.plan_id, db=db) if session and session.plan_id else None
    session_tasks = await ExperimentPlans.session_tasks(session.id, db=db) if session and session.plan_id else []
    summaries = []
    for item in session_tasks:
        count = None
        if item.task_type == ExperimentTaskType.QUESTION:
            task = await QuestionTasks.get_task(item.question_task_id, db=db)
            count = len(task.questions) if task else 0
        summaries.append(
            ExperimentTaskSummary(
                id=item.id,
                position=item.position,
                task_type=item.task_type,
                title=item.title,
                status=item.status,
                question_count=count,
                essay_topic=(
                    ExperimentTopicResponse(
                        id=item.essay_topic_id, title=item.essay_topic_title, question=item.essay_topic_question
                    )
                    if item.task_type == ExperimentTaskType.ESSAY and item.essay_topic_id
                    else None
                ),
                survey_required=(item.survey_required if item.task_type == ExperimentTaskType.SURVEY else None),
            )
        )
    return ExperimentCurrentResponse(
        state=state,
        session_id=session.id if session else None,
        group_id=session.group_id if session else None,
        plan_id=session.plan_id if session else None,
        progression_mode=plan.progression_mode if plan else None,
        chat_mode=plan.chat_mode.value if plan else None,
        survey_variant=plan.survey_variant if plan else ('ESSAY' if session else None),
        tasks=summaries,
        topic=(
            ExperimentTopicResponse(id=session.topic_id, title=session.topic_title, question=session.topic_question)
            if session
            and session.topic_id
            and state in {ExperimentState.TOPIC_REQUIRED, ExperimentState.TASK_REQUIRED, ExperimentState.IN_PROGRESS}
            else None
        ),
        agreement_text=EXPERIMENT_AGREEMENT_TEXT if state == ExperimentState.CONSENT_REQUIRED else None,
        error=error,
    )


async def _repair_finalized_question_submissions(request: Request, user, db: AsyncSession):
    """Repair legacy split-brain rows before returning participant state."""
    rows = (
        (
            await db.execute(
                select(QuestionSubmission)
                .join(ExperimentSessionTask, ExperimentSessionTask.id == QuestionSubmission.session_task_id)
                .join(ExperimentSession, ExperimentSession.id == ExperimentSessionTask.experiment_session_id)
                .where(
                    ExperimentSession.user_id == user.id,
                    ExperimentSessionTask.task_type == ExperimentTaskType.QUESTION.value,
                    ExperimentSessionTask.status == SessionTaskStatus.FINALIZED.value,
                    QuestionSubmission.status == 'DRAFT',
                )
            )
        )
        .scalars()
        .all()
    )
    pending_jobs = []
    for submission in rows:
        pending, _ = await QuestionSubmissions.finalize(
            submission.id,
            user.id,
            db=db,
            commit=False,
        )
        pending_jobs.append((pending, submission.id))
    if rows:
        await db.commit()
        for response_ids, submission_id in pending_jobs:
            await _schedule_grading(request, user, response_ids, submission_id)


@router.get('/current', response_model=ExperimentCurrentResponse)
async def current(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await _repair_finalized_question_submissions(request, user, db)
    return await response_for(user, db)


@router.post('/current/consent', response_model=ExperimentCurrentResponse)
async def consent(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.consent(user, db)
    return await response_for(user, db)


@router.post('/current/pre-survey', response_model=ExperimentCurrentResponse)
async def pre_survey(
    form: PreSurveyForm | TaskNeutralPreSurveyForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await Experiments.submit_pre_survey(user, form, db)
    return await response_for(user, db)


@router.post('/current/start', response_model=ExperimentCurrentResponse)
async def start(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.start(user, db)
    return await response_for(user, db)


@router.post('/current/post-survey', response_model=ExperimentCurrentResponse)
async def post_survey(
    form: PostSurveyForm | TaskNeutralPostSurveyForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await Experiments.submit_post_survey(user, form, db)
    return await response_for(user, db)


@router.post('/current/complete', response_model=ExperimentCurrentResponse)
async def complete(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.complete(user, db)
    return await response_for(user, db)


async def _session_task_for_user(task_id: str, user, db: AsyncSession, editable: bool = True):
    row = (
        await db.execute(
            select(ExperimentSessionTask, ExperimentSession)
            .join(ExperimentSession, ExperimentSession.id == ExperimentSessionTask.experiment_session_id)
            .where(ExperimentSessionTask.id == task_id, ExperimentSession.user_id == user.id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail='Experiment task not found.')
    task, session = row
    if not editable and task.status in {
        SessionTaskStatus.FINALIZED.value,
        SessionTaskStatus.SKIPPED.value,
    }:
        return task, session
    if session.state != ExperimentState.IN_PROGRESS.value:
        raise HTTPException(status_code=409, detail='Experiment tasks are not currently editable.')
    if task.status == SessionTaskStatus.LOCKED.value or (editable and task.status == SessionTaskStatus.FINALIZED.value):
        raise HTTPException(status_code=409, detail='Experiment task is locked or finalized.')
    return task, session


@router.get('/current/tasks/{task_id}')
async def get_current_task(
    task_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    task, session = await _session_task_for_user(task_id, user, db, editable=False)
    if task.task_type == ExperimentTaskType.ESSAY.value:
        return {
            'id': task.id,
            'task_type': task.task_type,
            'title': task.title,
            'status': task.status,
            'draft': task.essay_draft or '',
            'topic': {
                'id': task.essay_topic_id,
                'title': task.essay_topic_title,
                'question': task.essay_topic_question,
            },
        }
    if task.task_type == ExperimentTaskType.SURVEY.value:
        survey_task = await SurveyTasks.participant_task(task.survey_task_id, db=db)
        submission = await SurveySubmissions.get_or_create(task, user.id, db)
        await db.commit()
        return {
            'id': task.id,
            'task_type': task.task_type,
            'title': task.title,
            'status': task.status,
            'required': task.survey_required,
            'survey_task': survey_task,
            'submission': await SurveySubmissions.participant_model(submission.id, user.id, db=db),
        }
    question_task = await QuestionTasks.participant_task(task.question_task_id, db=db)
    submission = await QuestionSubmissions.get_or_create(task, user.id, db)
    await db.commit()
    return {
        'id': task.id,
        'task_type': task.task_type,
        'title': task.title,
        'status': task.status,
        'question_task': question_task,
        'submission': await QuestionSubmissions.participant_model(submission.id, user.id, db=db),
    }


@router.patch('/current/tasks/{task_id}/essay-draft')
async def save_essay_draft(
    task_id: str, form: EssayDraftForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    task, _ = await _session_task_for_user(task_id, user, db)
    if task.task_type != ExperimentTaskType.ESSAY.value:
        raise HTTPException(status_code=422, detail='This is not an Essay Task.')
    task.essay_draft = form.content
    task.updated_at = int(time.time_ns())
    await db.commit()
    return {'status': True, 'updated_at': task.updated_at}


@router.put('/current/tasks/{task_id}/question-draft')
async def save_question_draft(
    task_id: str,
    form: QuestionDraftForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    task, _ = await _session_task_for_user(task_id, user, db)
    if task.task_type != ExperimentTaskType.QUESTION.value:
        raise HTTPException(status_code=422, detail='This is not a Question Task.')
    return await QuestionSubmissions.save_draft(task.id, user.id, form, db=db)


@router.post('/current/tasks/{task_id}/survey')
async def submit_survey_task(
    task_id: str,
    form: SurveyDraftForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    task, session = await _session_task_for_user(task_id, user, db, editable=False)
    if task.status in {SessionTaskStatus.FINALIZED.value, SessionTaskStatus.SKIPPED.value}:
        return {
            'experiment': await response_for(user, db),
            'submission': await SurveySubmissions.for_session_task(task.id, user.id, db=db),
        }
    if task.task_type != ExperimentTaskType.SURVEY.value or task.status != SessionTaskStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail='This survey is not currently active.')
    submission = await SurveySubmissions.finalize(task, user.id, form, db, commit=False)
    now = int(time.time_ns())
    task = await db.get(ExperimentSessionTask, task.id)
    task.status = SessionTaskStatus.FINALIZED.value
    task.completed_at = task.finalized_at = now
    task.updated_at = now
    await Experiments.advance_after_stage(await db.get(ExperimentSession, session.id), task, db)
    await db.commit()
    return {'experiment': await response_for(user, db), 'submission': submission}


@router.post('/current/tasks/{task_id}/skip-survey')
async def skip_survey_task(
    task_id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    task, session = await _session_task_for_user(task_id, user, db, editable=False)
    if task.status in {SessionTaskStatus.FINALIZED.value, SessionTaskStatus.SKIPPED.value}:
        return {
            'experiment': await response_for(user, db),
            'submission': await SurveySubmissions.for_session_task(task.id, user.id, db=db),
        }
    if task.task_type != ExperimentTaskType.SURVEY.value or task.status != SessionTaskStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail='This survey is not currently active.')
    submission = await SurveySubmissions.skip(task, user.id, db, commit=False)
    now = int(time.time_ns())
    task = await db.get(ExperimentSessionTask, task.id)
    task.status = SessionTaskStatus.SKIPPED.value
    task.completed_at = task.finalized_at = now
    task.updated_at = now
    await Experiments.advance_after_stage(await db.get(ExperimentSession, session.id), task, db)
    await db.commit()
    return {'experiment': await response_for(user, db), 'submission': submission}


async def _unlock_after_review(task: ExperimentSessionTask, session: ExperimentSession, db: AsyncSession):
    plan = await ExperimentPlans.get_plan(session.plan_id, db=db)
    if plan.progression_mode != ProgressionMode.SEQUENTIAL_REVIEW:
        raise HTTPException(status_code=409, detail='This experiment does not use review progression.')
    now = int(time.time_ns())
    task.status = SessionTaskStatus.COMPLETED.value
    task.completed_at = now
    task.updated_at = now
    next_task = (
        (
            await db.execute(
                select(ExperimentSessionTask)
                .where(
                    ExperimentSessionTask.experiment_session_id == session.id,
                    ExperimentSessionTask.position > task.position,
                )
                .order_by(ExperimentSessionTask.position)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if (
        next_task
        and next_task.task_type != ExperimentTaskType.SURVEY.value
        and next_task.status == SessionTaskStatus.LOCKED.value
    ):
        next_task.status = SessionTaskStatus.ACTIVE.value
        next_task.started_at = now
        next_task.updated_at = now
    await db.commit()


@router.post('/current/tasks/{task_id}/complete')
async def mark_task_complete(
    task_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    task, session = await _session_task_for_user(task_id, user, db, editable=False)
    if task.status == SessionTaskStatus.FINALIZED.value:
        return await response_for(user, db)
    if task.task_type == ExperimentTaskType.ESSAY.value and not (task.essay_draft or '').strip():
        raise HTTPException(status_code=422, detail='Essay content is required before completing this task.')
    await _unlock_after_review(task, session, db)
    return await response_for(user, db)


async def _schedule_grading(request: Request, user, response_ids: list[str], submission_id: str):
    for response_id in response_ids:
        await create_task(request.app.state.redis, grade_response(request, user, response_id), submission_id)


@router.post('/current/tasks/{task_id}/finalize/essay')
async def finalize_essay(
    task_id: str,
    request: Request,
    form: EssayDraftForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    task, session = await _session_task_for_user(task_id, user, db, editable=False)
    if task.status == SessionTaskStatus.FINALIZED.value:
        return await response_for(user, db)
    plan = await ExperimentPlans.get_plan(session.plan_id, db=db)
    if plan.progression_mode != ProgressionMode.STRICT_SEQUENTIAL:
        raise HTTPException(status_code=409, detail='This experiment is finalized as a whole.')
    content = form.content.strip()
    if task.task_type != ExperimentTaskType.ESSAY.value or not content:
        raise HTTPException(status_code=422, detail='Essay content is required.')
    await Experiments.finalize_essay_task(user, task, content, db)
    return await response_for(user, db)


@router.post('/current/tasks/{task_id}/finalize/questions')
async def finalize_questions(
    task_id: str,
    request: Request,
    form: QuestionDraftForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    task, session = await _session_task_for_user(task_id, user, db, editable=False)
    if task.status == SessionTaskStatus.FINALIZED.value:
        submission = await QuestionSubmissions.get_or_create(task, user.id, db)
        pending = []
        if submission.status != 'FINALIZED':
            pending, result = await QuestionSubmissions.finalize(
                submission.id,
                user.id,
                db=db,
                commit=False,
            )
            await db.commit()
            await _schedule_grading(request, user, pending, submission.id)
        else:
            result = await QuestionSubmissions.participant_model(submission.id, user.id, db=db)
        return {
            'experiment': await response_for(user, db),
            'submission': result,
        }
    plan = await ExperimentPlans.get_plan(session.plan_id, db=db)
    if (
        plan.progression_mode != ProgressionMode.STRICT_SEQUENTIAL
        or task.task_type != ExperimentTaskType.QUESTION.value
    ):
        raise HTTPException(status_code=409, detail='This Question Task cannot be finalized individually.')
    submission = await QuestionSubmissions.save_draft(task.id, user.id, form, db=db, commit=False)
    pending, result = await QuestionSubmissions.finalize(submission.id, user.id, db=db, commit=False)
    finalized_submission = await db.get(QuestionSubmission, submission.id)
    if not finalized_submission or finalized_submission.status != 'FINALIZED':
        raise HTTPException(status_code=500, detail='Question submission finalization did not persist.')
    task = await db.get(ExperimentSessionTask, task.id)
    task.status = SessionTaskStatus.FINALIZED.value
    task.completed_at = task.finalized_at = int(time.time_ns())
    await Experiments._advance_after_task(await db.get(ExperimentSession, session.id), task, db)
    await db.commit()
    await _schedule_grading(request, user, pending, submission.id)
    return {'experiment': await response_for(user, db), 'submission': result}


@router.post('/current/finalize-tasks')
async def finalize_all_tasks(
    request: Request, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    state, session_model, _ = await Experiments.get_current(user, db=db)
    if state != ExperimentState.IN_PROGRESS or not session_model or not session_model.plan_id:
        raise HTTPException(status_code=409, detail='No ordered experiment is ready to finalize.')
    plan = await ExperimentPlans.get_plan(session_model.plan_id, db=db)
    if plan.progression_mode == ProgressionMode.STRICT_SEQUENTIAL:
        raise HTTPException(status_code=409, detail='Strict sequential tasks are finalized individually.')
    rows = list(
        (
            await db.execute(
                select(ExperimentSessionTask)
                .where(ExperimentSessionTask.experiment_session_id == session_model.id)
                .order_by(ExperimentSessionTask.position)
            )
        )
        .scalars()
        .all()
    )
    task_rows = [row for row in rows if row.task_type != ExperimentTaskType.SURVEY.value]
    if plan.progression_mode == ProgressionMode.SEQUENTIAL_REVIEW and any(
        row.status not in {SessionTaskStatus.COMPLETED.value, SessionTaskStatus.FINALIZED.value} for row in task_rows
    ):
        raise HTTPException(status_code=422, detail='Complete every task before final submission.')
    empty_essay = next(
        (
            row
            for row in rows
            if row.status != SessionTaskStatus.FINALIZED.value
            and row.task_type == ExperimentTaskType.ESSAY.value
            and not (row.essay_draft or '').strip()
        ),
        None,
    )
    if empty_essay:
        raise HTTPException(status_code=422, detail=f'Essay Task "{empty_essay.title}" is empty.')
    pending_jobs: list[tuple[list[str], str]] = []
    for row in task_rows:
        if row.status in {SessionTaskStatus.FINALIZED.value, SessionTaskStatus.SKIPPED.value}:
            continue
        if row.task_type == ExperimentTaskType.ESSAY.value:
            content = (row.essay_draft or '').strip()
            await Experiments.finalize_essay_task(user, row, content, db)
        else:
            submission = await QuestionSubmissions.get_or_create(row, user.id, db)
            pending, _ = await QuestionSubmissions.finalize(
                submission.id,
                user.id,
                db=db,
                commit=False,
            )
            finalized_submission = await db.get(QuestionSubmission, submission.id)
            if not finalized_submission or finalized_submission.status != 'FINALIZED':
                raise HTTPException(status_code=500, detail='Question submission finalization did not persist.')
            row = await db.get(ExperimentSessionTask, row.id)
            row.status = SessionTaskStatus.FINALIZED.value
            row.completed_at = row.finalized_at = int(time.time_ns())
            await db.commit()
            pending_jobs.append((pending, submission.id))
    session = await db.get(ExperimentSession, session_model.id)
    if task_rows:
        last_task = max(task_rows, key=lambda item: item.position)
        await Experiments.advance_after_stage(session, last_task, db, task_block_finalized=True)
    else:
        session.state = ExperimentState.THANK_YOU_REQUIRED.value
        session.updated_at = int(time.time_ns())
    await db.commit()
    for response_ids, submission_id in pending_jobs:
        await _schedule_grading(request, user, response_ids, submission_id)
    return await response_for(user, db)
