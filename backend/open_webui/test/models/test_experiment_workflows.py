import asyncio
import io
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from open_webui.models.experiment_workflows import (
    WORKFLOW_FORMAT,
    WORKFLOW_SCHEMA_VERSION,
    ExperimentWorkflows,
    _expand_random_all,
    _upgrade_v2_definition,
    default_workflow_definition,
    normalize_definition,
    validate_definition,
)


def run(coro):
    return asyncio.run(coro)


def ready_essay_workflow():
    definition = default_workflow_definition()
    definition['items'] = [
        {
            'key': 'item',
            'task_type': 'ESSAY',
            'title': 'Essay',
            'essay_topic_mode': 'SPECIFIC',
            'essay_topic_id': 'topic-id',
            'essay_topic_ids': [],
            'enabled': True,
        }
    ]
    return definition


def archive_bytes(manifest, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for path, contents in extra or []:
            archive.writestr(path, contents)
    return output.getvalue()


def test_incomplete_workflow_is_a_draft_but_reference_plan_is_structurally_ready():
    assert validate_definition(default_workflow_definition())
    assert validate_definition(ready_essay_workflow()) == []


def test_random_selected_requires_two_existing_topic_references():
    definition = ready_essay_workflow()
    definition['items'][0].update(
        essay_topic_mode='RANDOM_SELECTED',
        essay_topic_id=None,
        essay_topic_ids=['topic-id'],
    )

    issues = validate_definition(definition)

    assert any('at least two essay topics' in issue.message for issue in issues)


def test_random_all_requires_an_explicit_saved_pool():
    definition = ready_essay_workflow()
    definition['items'][0].update(
        essay_topic_mode='RANDOM_ALL',
        essay_topic_id=None,
        essay_topic_ids=[],
    )

    issues = validate_definition(definition)

    assert any(issue.path == 'items.0.essay_topic_ids' for issue in issues)


def test_random_all_is_expanded_from_the_ordinary_topic_library():
    result = MagicMock()
    result.scalars.return_value = ['topic-a', 'topic-b']
    db = AsyncMock()
    db.execute.return_value = result
    definition = ready_essay_workflow()
    definition['items'][0].update(
        essay_topic_mode='RANDOM_ALL',
        essay_topic_id=None,
        essay_topic_ids=[],
    )

    expanded = run(_expand_random_all(definition, db))

    assert expanded['items'][0]['essay_topic_ids'] == ['topic-a', 'topic-b']
    assert definition['items'][0]['essay_topic_ids'] == []


def test_import_rejects_version_one_without_writes():
    db = AsyncMock()
    data = archive_bytes(
        {
            'format': WORKFLOW_FORMAT,
            'schema_version': 1,
            'workflow': {
                'name': 'Legacy',
                'description': '',
                'definition': ready_essay_workflow(),
            },
        }
    )

    with pytest.raises(HTTPException) as error:
        run(ExperimentWorkflows.import_archive(data, 'admin', True, db))

    assert error.value.status_code == 422
    assert 'unsupported' in error.value.detail
    db.rollback.assert_awaited_once()
    db.add.assert_not_called()


def test_version_two_definition_strips_disabled_memory_injection():
    definition = ready_essay_workflow()
    definition['conditions'][0]['memory_injection'] = {
        'enabled': False,
        'content': 'discarded legacy value',
    }

    upgraded = _upgrade_v2_definition(definition)

    assert 'memory_injection' not in upgraded['conditions'][0]
    assert 'memory_injection' in definition['conditions'][0]


def test_version_two_definition_rejects_enabled_memory_injection():
    definition = ready_essay_workflow()
    definition['conditions'][0]['memory_injection'] = {
        'enabled': True,
        'content': 'legacy context',
    }

    with pytest.raises(HTTPException) as error:
        _upgrade_v2_definition(definition)

    assert error.value.status_code == 422
    assert 'explicitly disabled' in error.value.detail


@pytest.mark.parametrize('memory', [{}, True, {'enabled': 1}])
def test_version_two_definition_rejects_ambiguous_memory_injection(memory):
    definition = ready_essay_workflow()
    definition['conditions'][0]['memory_injection'] = memory

    with pytest.raises(HTTPException) as error:
        _upgrade_v2_definition(definition)

    assert error.value.status_code == 422
    assert 'explicitly disabled' in error.value.detail


def test_current_workflow_writes_reject_memory_injection():
    definition = ready_essay_workflow()
    definition['conditions'][0]['memory_injection'] = {'enabled': False}

    with pytest.raises(HTTPException) as error:
        normalize_definition(definition)

    assert error.value.status_code == 422


def test_import_rejects_path_traversal():
    db = AsyncMock()
    manifest = {
        'format': WORKFLOW_FORMAT,
        'schema_version': WORKFLOW_SCHEMA_VERSION,
        'workflow': {
            'name': 'Unsafe',
            'description': '',
            'definition': ready_essay_workflow(),
        },
        'resources': {
            'essay_topics': [],
            'question_tasks': [],
            'survey_tasks': [],
            'assets': [],
        },
    }

    with pytest.raises(HTTPException) as error:
        run(
            ExperimentWorkflows.import_archive(
                archive_bytes(manifest, [('../payload', b'x')]),
                'admin',
                True,
                db,
            )
        )

    assert error.value.status_code == 422
    assert 'unsafe path' in error.value.detail
