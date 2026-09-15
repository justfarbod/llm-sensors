"""Workflow analytics regressions: configuration boundaries and attribution."""
import asyncio
from copy import deepcopy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from open_webui.internal.db import Base
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.experiments import ExperimentSession
from open_webui.models.experiment_plans import ExperimentPlan, ExperimentPlanItem, ExperimentSessionTask
from open_webui.models.experiment_perturbations import ExperimentCondition
from open_webui.models.essays import EssayTopic
from open_webui.models.groups import Group, GroupMember
from open_webui.models.users import User
from open_webui.models.question_submissions import QuestionSubmission
from open_webui.models.survey_submissions import SurveySubmission
from open_webui.routers.experiment_analytics import DashboardFilters
from open_webui.utils import workflow_results as w
from open_webui.utils.workflow_provenance import configuration_content, content_hash

NS = 1_000_000_000


def run_db(check):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as conn:
                tables = [table for name, table in Base.metadata.tables.items() if name.startswith(('experiment_', 'question_', 'survey_')) or name in {'group', 'group_member', 'user', 'chat', 'chat_message', 'essay', 'essay_topic', 'file'}]
                await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await check(db)
        finally:
            await engine.dispose()
    asyncio.run(run())


def session(id, plan=None, user='u', group='g', **kwargs):
    return ExperimentSession(id=id, plan_id=plan, user_id=user, group_id=group, state='COMPLETED',
                             created_at=NS, updated_at=20*NS, completed_at=20*NS, **kwargs)


def task(id, sid, position=0, **kwargs):
    return ExperimentSessionTask(id=id, experiment_session_id=sid, position=position,
        task_type=kwargs.pop('task_type','QUESTION'), title=id, status=kwargs.pop('status','FINALIZED'),
        started_at=2*NS, finalized_at=10*NS, created_at=NS, updated_at=10*NS, **kwargs)


def test_linked_usage_is_counted_once_and_consistent_across_filter_subsets():
    async def check(db):
        db.add_all([session('a','p'),session('b','p',group='second'),session('old',user='old'),session('overlap1',user='ambiguous'),session('overlap2',user='ambiguous',group='second'),task('bio','a'),task('geo','a',1),task('other','b'),
            Chat(id='shared',user_id='u',experiment_session_id='a'),Chat(id='per-task',user_id='u',experiment_session_task_id='geo'),
            Chat(id='personal',user_id='u'),Chat(id='other-chat',user_id='u',experiment_session_id='b'),Chat(id='old-chat',user_id='old'),Chat(id='ambiguous-chat',user_id='ambiguous')])
        def message(id,chat,role='user',tid=None,user='u'):
            return ChatMessage(id=id,chat_id=chat,user_id=user,role=role,created_at=5,experiment_session_task_id=tid,
                               usage={'input_tokens':10,'output_tokens':20} if role=='assistant' else None)
        db.add_all([message('1','shared',tid='bio'),message('2','shared','assistant','bio'),message('3','per-task'),
                    message('4','shared'),message('5','personal'),message('6','other-chat',tid='other'),
                    message('7','old-chat',user='old'),message('8','ambiguous-chat',user='ambiguous')])
        await db.commit()
        all_usage = await w.usage_by_session(db,['a','b','old','overlap1','overlap2'])
        assert all_usage['a']['prompts'] == 3 and all_usage['a']['responses'] == 1
        assert all_usage['a']['by_task']['geo']['prompts'] == 1
        assert all_usage['a']['unattributed']['prompts'] == 1
        assert all_usage['a']['total_tokens'] == 30
        assert all_usage['old']['attribution'] == 'legacy_time_window'
        assert all_usage['overlap1']['prompts'] == 0
        assert (await w.usage_by_session(db,['overlap1']))['overlap1']['prompts'] == 0
        assert (await w.usage_by_session(db,['a']))['a'] == all_usage['a']
        details=[{'session_task_id':'bio'},{'session_task_id':'geo'}]
        unassigned=await w.attach_task_activity(db,session('a','p'),details,all_usage['a'])
        assert len(details[0]['messages']) == 2 and len(details[1]['messages']) == 1 and len(unassigned) == 1
    run_db(check)


def test_configuration_grouping_historical_identity_repeated_steps_and_pending_grades():
    async def check(db):
        db.add_all([User(id='u',name='Real',role='user'),User(id='demo',name='Demo',role='user',info={'synthetic':True}),
                    Group(id='g',name='One'),Group(id='g2',name='Two')])
        for id, group, fingerprint, created, source in [('p','g','same',1,'deleted-source'),('p2','g2','same',2,'deleted-source'),('p3','g','changed',3,'deleted-source'),('empty','g','empty',4,'deleted-source'),('legacy','g','same',1,None)]:
            db.add(ExperimentPlan(id=id,group_id=group,configuration_fingerprint=fingerprint,source_workflow_id=source,
                source_workflow_name='Captured name' if source else None,source_workflow_revision=1 if source else None,
                created_at=created*NS,updated_at=created*NS,status='PUBLISHED'))
            for position in range(2):
                db.add(ExperimentPlanItem(id=f'{id}-{position}',plan_id=id,position=position,task_type='QUESTION',title=f'Questions {position}',source_step_key=f'step-{position}'))
        db.add_all([session('s1','p'),session('s2','p2',user='demo',group='g2'),session('s3','p3',user='u3'),session('s4','legacy',user='u4')])
        for sid,pid in [('s1','p'),('s2','p2'),('s3','p3'),('s4','legacy')]:
            for position in range(2):
                tid=f'{sid}-{position}'
                db.add(task(tid,sid,position,plan_item_id=f'{pid}-{position}'))
                if position == 0:
                    db.add(QuestionSubmission(id=tid,session_task_id=tid,user_id='u',status='SUBMITTED',grading_status='PENDING',maximum_score=20,created_at=NS,updated_at=NS))
        await db.commit()
        listing=await w.workflow_list(db,DashboardFilters())
        assert len(listing['items']) == 2
        detail=await w.workflow_detail(db,'deleted-source',DashboardFilters())
        assert detail['configuration_id']=='changed' and detail['name']=='Captured name'
        detail=await w.workflow_detail(db,'deleted-source',DashboardFilters(configuration_id='same'))
        assert detail['runs']==2 and len(detail['steps'])==2
        assert detail['steps'][0]['pending_grades']==2 and detail['steps'][0]['average_score'] is None
        assert detail['steps'][0]['completed']==2
        assert detail['steps'][1]['completed']==0 and detail['steps'][1]['pending']==2
        assert (await w.workflow_detail(db,'deleted-source',DashboardFilters(configuration_id='same',data_kind='demo')))['runs']==1
        page1=await w.step_results(db,'deleted-source','step-0',DashboardFilters(configuration_id='same'),1,1)
        page2=await w.step_results(db,'deleted-source','step-0',DashboardFilters(configuration_id='same'),2,1)
        assert page1['total']==2 and page1['items'][0]['session_id'] != page2['items'][0]['session_id']
        assert (await w.workflow_detail(db,'legacy:legacy',DashboardFilters()))['workflow_origin']=='unknown'
    run_db(check)


def test_optional_survey_skip_and_missing_native_submission():
    async def check(db):
        tasks=[task('skip','s',task_type='SURVEY',survey_required=False,status='ACTIVE'),task('required','s',1,task_type='SURVEY'),task('essay','s',2,task_type='ESSAY')]
        db.add_all(tasks)
        db.add(SurveySubmission(id='submission',session_task_id='skip',user_id='u',status='SKIPPED',created_at=NS,updated_at=NS))
        await db.commit()
        states=await w.native_task_states(db,tasks)
        assert states == {'skip':'SKIPPED','required':'IN_PROGRESS','essay':'IN_PROGRESS'}
    run_db(check)


def test_fingerprint_ignores_deployment_ids_and_numeric_representation_but_tracks_content():
    async def check(db):
        db.add_all([EssayTopic(id='a',title='Topic',question='Evidence?',created_at=NS,updated_at=NS),EssayTopic(id='b',title='Topic',question='Evidence?',created_at=2*NS,updated_at=2*NS)])
        await db.commit()
        definition={'items':[{'key':'step-a','task_type':'ESSAY','title':'Write','essay_topic_mode':'SPECIFIC','essay_topic_id':'a','llm_prompt_budget':{'mode':'TASK','limit':100,'question_limits':{}}}], 'conditions':[]}
        first=content_hash(await configuration_content(db,definition=definition))
        copied=deepcopy(definition);copied['items'][0].update(key='different',essay_topic_id='b')
        assert content_hash(await configuration_content(db,definition=copied))==first
        copied['items'][0]['llm_prompt_budget']['limit']=99
        assert content_hash(await configuration_content(db,definition=copied))!=first
        assert content_hash({'delay':3})==content_hash({'delay':3.0})
    run_db(check)


def test_manual_grading_and_retry_preserve_review_in_disposable_database(tmp_path, monkeypatch):
    """Exercise the existing grading actions without calling a provider or touching live data."""
    import sqlite3
    from pathlib import Path
    from types import SimpleNamespace
    import pytest
    from fastapi import HTTPException
    from sqlalchemy import select
    from open_webui.models.question_submissions import QuestionResponse, QuestionGradingAttempt
    from open_webui.models.question_tasks import QuestionTaskQuestion
    from open_webui.routers import experiment_analytics as analytics
    source = Path(__file__).resolve().parents[3] / 'data/webui.db'
    if not source.is_file():
        pytest.skip('Local Grade 10 fixture database is unavailable')
    copy = tmp_path / 'grading.db'
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as live, sqlite3.connect(copy) as target:
        live.backup(target)
    queued = []

    async def queue(_redis, coroutine, key):
        coroutine.close()
        queued.append(key)

    monkeypatch.setattr(analytics, 'create_task', queue)
    monkeypatch.setattr('open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING', True)

    async def check():
        engine = create_async_engine(f'sqlite+aiosqlite:///{copy}')
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                record = (await db.execute(select(QuestionResponse, QuestionTaskQuestion)
                    .join(QuestionTaskQuestion, QuestionTaskQuestion.id == QuestionResponse.question_id)
                    .where(QuestionTaskQuestion.question_type == 'FREE_TEXT', QuestionResponse.effective_score.isnot(None)).limit(1))).first()
                if not record:
                    pytest.skip('No free-text example available')
                response, question = record
                admin = SimpleNamespace(id='synthetic-test-reviewer', role='admin')
                with pytest.raises(HTTPException) as error:
                    await analytics.override_question_score(response.id, analytics.ScoreOverrideForm(score=question.max_score + 1), admin, db)
                assert error.value.status_code == 422
                detail = await analytics.override_question_score(response.id, analytics.ScoreOverrideForm(score=0, note='Disposable regression test'), admin, db)
                reviewed = next(r for r in detail['responses'] if r['id'] == response.id)
                assert reviewed['effective_score'] == 0 and reviewed['overrides'][-1]['note'] == 'Disposable regression test'
                question.grading_mode = 'LLM_ASSISTED'
                await db.commit()
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
                retry = await analytics.retry_question_grading(response.id, request, admin, db)
                repeated = await analytics.retry_question_grading(response.id, request, admin, db)
                assert retry['attempt_id'] == repeated['attempt_id'] and len(queued) == 1
                assert response.effective_score == 0 and response.grading_method == 'ADMIN_OVERRIDE'
                assert (await db.get(QuestionGradingAttempt, retry['attempt_id'])).status == 'PENDING'
        finally:
            await engine.dispose()
    asyncio.run(check())
