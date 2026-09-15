import importlib
import json

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, JSON, MetaData, Table, Text, BigInteger, create_engine, select


def test_migration_preserves_workflows_and_defaults_to_100(tmp_path):
    migration = importlib.import_module('open_webui.migrations.versions.e8f9a0b1c2d3_add_experiment_prompt_budgets')
    engine = create_engine(f'sqlite:///{tmp_path}/migration.db')
    metadata = MetaData()
    workflows = Table(
        'experiment_workflow',
        metadata,
        Column('id', Text, primary_key=True),
        Column('definition', JSON),
        Column('revision', Integer),
        Column('updated_at', BigInteger),
    )
    for name in ('experiment_plan_item', 'experiment_session_task'):
        Table(name, metadata, Column('id', Text, primary_key=True), Column('task_type', Text))
    metadata.create_all(engine)
    definition = {
        'items': [
            {'key': 'essay', 'task_type': 'ESSAY', 'essay_topic_id': 'topic'},
            {'key': 'quiz', 'task_type': 'QUESTION', 'question_task_id': 'questions'},
            {'key': 'survey', 'task_type': 'SURVEY', 'survey_task_id': 'survey'},
        ],
        'conditions': [{'key': 'control'}],
        'chat_mode': 'SHARED_EXPERIMENT',
    }
    with engine.begin() as conn:
        conn.execute(workflows.insert().values(id='workflow', definition=definition, revision=5, updated_at=1))
        for name in ('experiment_plan_item', 'experiment_session_task'):
            conn.execute(
                metadata.tables[name].insert(),
                [{'id': item['key'], 'task_type': item['task_type']} for item in definition['items']],
            )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        result = conn.execute(select(workflows)).mappings().one()
        assert result['id'] == 'workflow' and result['revision'] == 6
        upgraded = result['definition']
        assert upgraded['conditions'] == definition['conditions']
        for old, new in zip(definition['items'], upgraded['items']):
            assert all(new[key] == value for key, value in old.items())
            if old['task_type'] != 'SURVEY':
                assert new['llm_prompt_budget']['limit'] == 100
        reflected = MetaData()
        reflected.reflect(conn)
        for name in ('experiment_plan_item', 'experiment_session_task'):
            rows = conn.execute(select(reflected.tables[name])).mappings().all()
            assert all(row['llm_prompt_budget']['limit'] == 100 for row in rows if row['task_type'] != 'SURVEY')
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert conn.execute(select(workflows.c.definition)).scalar_one() == definition
    engine.dispose()
