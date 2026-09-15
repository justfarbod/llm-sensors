from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiments import ExperimentState, Experiments, require_experiment_chat_access
from open_webui.utils.auth import get_verified_user


async def require_experiment_access_dependency(
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await require_experiment_chat_access(user, db=db)


async def require_chat_access_dependency(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role == 'admin':
        if request.url.path == '/api/v1/chats/all/db':
            return ExperimentState.NOT_APPLICABLE
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Administrators can only access the Admin Panel.',
        )

    state = await require_experiment_chat_access(user, db=db)
    if state != ExperimentState.NOT_APPLICABLE and (
        '/archived' in request.url.path
        or request.url.path.endswith('/archive')
        or request.url.path.endswith('/archive/all')
        or request.url.path.endswith('/unarchive/all')
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Archived chats are not available during this experiment session.',
        )
    return state


async def require_non_experiment_user_dependency(
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role == 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Administrators can only access the Admin Panel.',
        )
    state, _, _ = await Experiments.get_current(user, db=db)
    if state != ExperimentState.NOT_APPLICABLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='This feature is not available during the experiment session.',
        )
    return user


async def require_guarded_experiment_generation(request: Request, user=Depends(get_verified_user)):
    path = request.url.path
    if request.method == 'POST' and any(part in path for part in (
        '/chat', '/generate', '/completions', '/responses', '/messages'
    )):
        state, _, _ = await Experiments.get_current(user)
        if state != ExperimentState.NOT_APPLICABLE:
            raise HTTPException(status_code=403, detail='Use the experiment chat endpoint for LLM requests.')
