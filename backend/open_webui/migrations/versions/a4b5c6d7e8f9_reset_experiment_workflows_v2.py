"""reset legacy workflow documents for reference-based composer

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""

import json

import sqlalchemy as sa
from alembic import op


revision = 'a4b5c6d7e8f9'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    files = sa.table(
        'file',
        sa.column('id', sa.String()),
        sa.column('path', sa.Text()),
        sa.column('meta', sa.JSON()),
    )
    workflow_file_ids = []
    workflow_paths = []
    for row in bind.execute(sa.select(files.c.id, files.c.path, files.c.meta)).mappings():
        meta = row['meta']
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = None
        if isinstance(meta, dict) and meta.get('scope') == 'experiment-workflow':
            workflow_file_ids.append(row['id'])
            if row['path']:
                workflow_paths.append(row['path'])

    if workflow_file_ids:
        bind.execute(sa.delete(files).where(files.c.id.in_(workflow_file_ids)))
    bind.execute(sa.text('DELETE FROM experiment_workflow'))

    # Applied-plan snapshots use the question-task scope and are intentionally
    # retained. Only obsolete editor-owned workflow assets are removed here.
    if workflow_paths:
        try:
            from open_webui.storage.provider import Storage

            for path in workflow_paths:
                try:
                    Storage.delete_file(path)
                except Exception:
                    pass
        except Exception:
            pass


def downgrade():
    # The discarded version-1 workflow documents cannot be reconstructed.
    pass
