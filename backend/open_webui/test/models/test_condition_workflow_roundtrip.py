import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.internal.db import Base
from open_webui.models.essays import EssayTopic
from open_webui.models.experiment_perturbations import ExperimentConditionForm, ExperimentLLMRequest
from open_webui.models.experiment_plans import ExperimentPlanForm, ExperimentPlans
from open_webui.models.experiment_workflows import (
    ExperimentWorkflows,
    WorkflowApplyForm,
    WorkflowWriteForm,
    default_workflow_definition,
)
from open_webui.models.experiments import ExperimentSession, Experiments, ExperimentState
from open_webui.models.groups import Group
from open_webui.routers.experiments import (
    WarningTokenForm,
    _runtime_payload,
    warning_acknowledge,
    warning_display,
)
from open_webui.utils.experiment_perturbations import apply_nonstream_timing, prepare_request


def test_whole_group_workflow_persistence_runtime_and_existing_sessions(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/conditions.db')
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with factory() as session:
                yield session

    for module in ('models.experiment_plans', 'models.experiments', 'utils.experiment_perturbations'):
        monkeypatch.setattr(f'open_webui.{module}.get_async_db_context', context)

    async def run():
        try:
            async with engine.begin() as conn:
                tables = [table for name, table in Base.metadata.tables.items()
                          if name.startswith(('experiment_', 'question_', 'survey_'))
                          or name in {'group', 'user', 'essay', 'essay_topic', 'file'}]
                await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            async with factory() as db:
                group = Group(
                    id='group', name='Combined behavior group',
                    data={'config': {'experiment_mode_enabled': True}},
                    permissions={'features': {'essay_sidebar': True}},
                )
                db.add(group)
                db.add(EssayTopic(id='topic', title='Essay', question='Write an essay', created_at=1, updated_at=1))
                await db.commit()
                monkeypatch.setattr(
                    'open_webui.models.experiments.Groups.get_groups_by_member_id',
                    AsyncMock(return_value=[group]),
                )
                definition = default_workflow_definition()
                definition['items'] = [{
                    'key': 'essay', 'task_type': 'ESSAY', 'title': 'Essay',
                    'essay_topic_mode': 'SPECIFIC', 'essay_topic_id': 'topic',
                }]
                old_plan = await ExperimentPlans.save_for_group(
                    group.id, ExperimentPlanForm(items=definition['items']), db=db
                )
                existing_user = SimpleNamespace(id='existing', role='user')
                _, existing_session, error = await Experiments.get_current(existing_user, db=db)
                assert error is None
                assert existing_session.condition_id == old_plan.conditions[0].id

                definition['conditions'][0]['allocation_percent'] = 0
                combined = ExperimentConditionForm(
                    name='Warning + delay', allocation_percent=100,
                    warning_modal={'enabled': True, 'cadence': 'EVERY_N_PROMPTS', 'cadence_value': 1},
                    response_timing={'mode': 'DELAYED', 'delay_seconds': 2},
                )
                definition['conditions'].append({'key': 'combined', **combined.model_dump(exclude={'id'}, mode='json')})
                workflow = await ExperimentWorkflows.create(
                    WorkflowWriteForm(name='Whole group', definition=definition), 'admin', db
                )
                assert workflow.status == 'READY', workflow.issues
                blob, _ = await ExperimentWorkflows.export(workflow.id, db)
                imported = await ExperimentWorkflows.import_archive(blob, 'admin', True, db)
                assert imported.status == 'READY', imported.issues
                plan = await ExperimentWorkflows.apply(
                    imported.id,
                    WorkflowApplyForm(group_id=group.id, workflow_revision=imported.revision,
                                      expected_current_plan_version=old_plan.version),
                    'admin', True, db,
                )
                assert plan.source_workflow_id == imported.id
                assert plan.source_workflow_name == imported.name
                assert plan.source_workflow_revision == imported.revision
                assert plan.workflow_origin == 'recorded'
                assert len(plan.configuration_fingerprint) == 64
                assert plan.items[0].source_step_key == definition['items'][0]['key']
                assert plan.conditions[0].allocation_percent == 0
                assert plan.conditions[1].warning_modal == combined.warning_modal
                assert plan.conditions[1].response_timing == combined.response_timing

                for index in range(3):
                    user = SimpleNamespace(id=f'new-{index}', role='user')
                    _, session, error = await Experiments.get_current(user, db=db)
                    assert error is None
                    assert session.plan_id == plan.id
                    assert session.condition_id == plan.conditions[1].id
                    _, repeated, _ = await Experiments.get_current(user, db=db)
                    assert repeated.id == session.id
                    assert repeated.condition_assignment_id == session.condition_assignment_id

                row = await db.get(ExperimentSession, session.id)
                row.state = ExperimentState.IN_PROGRESS.value
                await db.commit()
                assert (await _runtime_payload(user, db))['warning'] is None
                sleeps = AsyncMock()
                monkeypatch.setattr('open_webui.utils.experiment_perturbations.asyncio.sleep', sleeps)
                for prompt in (1, 2):
                    request = await prepare_request(session, plan, None, {'user_message_id': f'message-{prompt}'})
                    assert request.timing.mode == 'DELAYED'
                    response = {'choices': [{'message': {'role': 'assistant', 'content': 'Answer'}}]}
                    assert await apply_nonstream_timing(response, request) == response
                    sleeps.assert_awaited_with(2)
                    warning = (await _runtime_payload(user, db))['warning']
                    assert warning['message'] == combined.warning_modal.message
                    token = WarningTokenForm(token=warning['token'])
                    assert await warning_display(token, user, db) == {'status': True}
                    assert await warning_acknowledge(token, user, db) == {'status': True}
                    assert (await _runtime_payload(user, db))['warning'] is None
                    recorded = await db.get(ExperimentLLMRequest, request.request_id)
                    assert recorded.condition_id == session.condition_id
                    assert recorded.timing_mode == 'DELAYED'
                    assert recorded.status == 'COMPLETED'

                # Publishing from the direct plan editor preserves the combined settings too.
                edited = await ExperimentPlans.save_for_group(group.id, ExperimentPlanForm(
                    items=[item.model_dump(exclude={'position'}) for item in plan.items],
                    conditions=[condition.model_dump() for condition in plan.conditions],
                ), db=db)
                assert edited.id != plan.id
                assert edited.conditions[0].allocation_percent == 0
                assert edited.conditions[1].warning_modal == combined.warning_modal
                assert edited.conditions[1].response_timing == combined.response_timing
                _, latest, _ = await Experiments.get_current(SimpleNamespace(id='latest', role='user'), db=db)
                assert latest.plan_id == edited.id
                assert latest.condition_id == edited.conditions[1].id
                for participant, original in ((existing_user, existing_session), (user, session)):
                    _, preserved, _ = await Experiments.get_current(participant, db=db)
                    assert preserved.plan_id == original.plan_id
                    assert preserved.condition_id == original.condition_id
                    assert preserved.condition_assignment_id == original.condition_assignment_id
        finally:
            await engine.dispose()

    asyncio.run(run())
