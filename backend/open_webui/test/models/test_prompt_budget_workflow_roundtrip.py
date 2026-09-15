import asyncio
import io
import json
import zipfile
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.internal.db import Base
from open_webui.models.groups import Group
from open_webui.models.experiments import ExperimentSession  # Register the session FK target.
from open_webui.models.experiment_plans import ExperimentPlans, ExperimentPlanForm, ExperimentPlanItem
from open_webui.models.experiment_workflows import (
    ExperimentWorkflows,
    WorkflowWriteForm,
    WorkflowApplyForm,
    default_workflow_definition,
)
from open_webui.models.question_tasks import QuestionTasks, QuestionTaskForm


def archive_bytes(manifest):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
    return out.getvalue()


@pytest.mark.parametrize('version', [2, 3, 4])
def test_question_budget_export_import_apply_and_snapshot(tmp_path, monkeypatch, version):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/workflow.db')
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with factory() as session:
                yield session

    monkeypatch.setattr('open_webui.models.question_tasks.get_async_db_context', context)
    monkeypatch.setattr('open_webui.models.experiment_plans.get_async_db_context', context)

    async def run():
        async with engine.begin() as conn:
            tables = [table for name, table in Base.metadata.tables.items()
                      if name.startswith(('experiment_', 'question_', 'survey_'))
                      or name in {'group', 'user', 'essay', 'essay_topic', 'file'}]
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        async with factory() as db:
            db.add(Group(id='group', name='Test', permissions={'features': {'essay_sidebar': True}}))
            await db.commit()
            questions = await QuestionTasks.create_task(
                QuestionTaskForm(
                    title='Quiz',
                    questions=[
                        {
                            'title': f'Question {i}',
                            'description': 'Choose one',
                            'question_type': 'SINGLE_CHOICE',
                            'max_score': 1,
                            'grading_mode': 'AUTOMATIC',
                            'choices': [{'text': 'Yes', 'is_correct': True}, {'text': 'No', 'is_correct': False}],
                        }
                        for i in (1, 2)
                    ],
                ),
                db=db,
            )
            questions = await QuestionTasks.publish(questions.id, True, db=db)
            definition = default_workflow_definition()
            limits = {questions.questions[0].id: 0, questions.questions[1].id: 7}
            definition['items'] = [
                {
                    'key': 'quiz',
                    'task_type': 'QUESTION',
                    'title': 'Quiz',
                    'question_task_id': questions.id,
                    'llm_prompt_budget': {'mode': 'PER_QUESTION', 'limit': None, 'question_limits': limits},
                }
            ]
            workflow = await ExperimentWorkflows.create(
                WorkflowWriteForm(name='Budget quiz', definition=definition), 'admin', db
            )
            assert workflow.status == 'READY', workflow.issues
            blob, _ = await ExperimentWorkflows.export(workflow.id, db)
            manifest = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read('manifest.json'))
            assert manifest['schema_version'] == 4
            portable_limits = manifest['workflow']['definition']['items'][0]['llm_prompt_budget']['question_limits']
            assert set(portable_limits.values()) == {0, 7}
            assert not set(portable_limits) & set(limits)
            if version != 4:
                manifest['schema_version'] = version
                manifest['workflow']['definition']['items'][0].pop('llm_prompt_budget')
                for question in manifest['resources']['question_tasks'][0]['questions']:
                    question.pop('key')
            imported = await ExperimentWorkflows.import_archive(archive_bytes(manifest), 'admin', True, db)
            assert imported.status == 'READY', imported.issues
            item = imported.definition['items'][0]
            imported_task = await QuestionTasks.get_task(item['question_task_id'], db=db)
            imported_ids = {question.id for question in imported_task.questions}
            assert not imported_ids & set(limits)
            if version == 4:
                assert set(item['llm_prompt_budget']['question_limits']) == imported_ids
                assert list(item['llm_prompt_budget']['question_limits'].values()) == [0, 7]
            else:
                assert item['llm_prompt_budget']['mode'] == 'TASK'
                assert item['llm_prompt_budget']['limit'] == 100
            plan = await ExperimentWorkflows.apply(
                imported.id, WorkflowApplyForm(group_id='group', workflow_revision=imported.revision), 'admin', True, db
            )
            applied = plan.items[0]
            applied_task = await QuestionTasks.get_task(applied.question_task_id, db=db)
            applied_ids = {question.id for question in applied_task.questions}
            assert not applied_ids & imported_ids
            if version == 4:
                assert set(applied.llm_prompt_budget.question_limits) == applied_ids
                assert list(applied.llm_prompt_budget.question_limits.values()) == [0, 7]
            else:
                assert applied.llm_prompt_budget.limit == 100
            session_tasks = await ExperimentPlans.instantiate_session_tasks('student-session', plan.id, db)
            await db.commit()
            assert session_tasks[0].llm_prompt_budget == applied.llm_prompt_budget
            # The group-plan editor also persists the configuration, with reference validation.
            form_item = applied.model_dump(exclude={'position'})
            edited = await ExperimentPlans.save_for_group('group', ExperimentPlanForm(items=[form_item]), db=db)
            assert edited.items[0].llm_prompt_budget == applied.llm_prompt_budget
            form_item['llm_prompt_budget'] = {'mode': 'PER_QUESTION', 'limit': None, 'question_limits': {'foreign': 1}}
            with pytest.raises(HTTPException) as error:
                await ExperimentPlans.save_for_group('group', ExperimentPlanForm(items=[form_item]), db=db)
            assert error.value.status_code == 422

        await engine.dispose()

    asyncio.run(run())
