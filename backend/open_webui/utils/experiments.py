from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiments import require_experiment_chat_access
from open_webui.utils.auth import get_verified_user


async def require_experiment_access_dependency(
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await require_experiment_chat_access(user, db=db)

