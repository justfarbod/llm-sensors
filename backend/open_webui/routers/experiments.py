from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.config import EXPERIMENT_AGREEMENT_TEXT
from open_webui.internal.db import get_async_session
from open_webui.models.experiments import (
    ExperimentSessionModel,
    ExperimentState,
    Experiments,
    PostSurveyForm,
    PreSurveyForm,
)
from open_webui.utils.auth import get_verified_user

router = APIRouter()


class ExperimentTopicResponse(BaseModel):
    id: str
    title: str
    question: str


class ExperimentCurrentResponse(BaseModel):
    state: ExperimentState
    session_id: Optional[str] = None
    group_id: Optional[str] = None
    topic: Optional[ExperimentTopicResponse] = None
    agreement_text: Optional[str] = None
    error: Optional[str] = None


async def response_for(user, db: AsyncSession):
    state, session, error = await Experiments.get_current(user, db=db)
    return ExperimentCurrentResponse(
        state=state,
        session_id=session.id if session else None,
        group_id=session.group_id if session else None,
        topic=(
            ExperimentTopicResponse(id=session.topic_id, title=session.topic_title, question=session.topic_question)
            if session and state in {ExperimentState.TOPIC_REQUIRED, ExperimentState.IN_PROGRESS}
            else None
        ),
        agreement_text=EXPERIMENT_AGREEMENT_TEXT if state == ExperimentState.CONSENT_REQUIRED else None,
        error=error,
    )


@router.get('/current', response_model=ExperimentCurrentResponse)
async def current(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    return await response_for(user, db)


@router.post('/current/consent', response_model=ExperimentCurrentResponse)
async def consent(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.consent(user, db)
    return await response_for(user, db)


@router.post('/current/pre-survey', response_model=ExperimentCurrentResponse)
async def pre_survey(form: PreSurveyForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.submit_pre_survey(user, form, db)
    return await response_for(user, db)


@router.post('/current/start', response_model=ExperimentCurrentResponse)
async def start(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.start(user, db)
    return await response_for(user, db)


@router.post('/current/post-survey', response_model=ExperimentCurrentResponse)
async def post_survey(form: PostSurveyForm, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.submit_post_survey(user, form, db)
    return await response_for(user, db)


@router.post('/current/complete', response_model=ExperimentCurrentResponse)
async def complete(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    await Experiments.complete(user, db)
    return await response_for(user, db)

