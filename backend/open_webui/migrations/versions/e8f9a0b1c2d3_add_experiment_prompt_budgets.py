"""Add task prompt budgets and preserve workflows with a default of 100.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""

import json
import time

import sqlalchemy as sa
from alembic import op

revision = 'e8f9a0b1c2d3'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    budget = {'mode': 'TASK', 'limit': 100, 'question_limits': {}}
    for name in ('experiment_plan_item', 'experiment_session_task'):
        op.add_column(name, sa.Column('llm_prompt_budget', sa.JSON(), nullable=True))
        table = sa.table(name, sa.column('task_type', sa.Text()), sa.column('llm_prompt_budget', sa.JSON()))
        bind.execute(
            table.update().where(table.c.task_type.in_(['ESSAY', 'QUESTION'])).values(llm_prompt_budget=budget)
        )
    workflows = sa.table(
        'experiment_workflow',
        sa.column('id', sa.Text()),
        sa.column('definition', sa.JSON()),
        sa.column('revision', sa.Integer()),
        sa.column('updated_at', sa.BigInteger()),
    )
    for row in bind.execute(sa.select(workflows)).mappings().all():
        definition = row['definition']
        if isinstance(definition, str):
            definition = json.loads(definition)
        changed = False
        for item in definition.get('items', []):
            if isinstance(item, dict) and item.get('task_type') in ('ESSAY', 'QUESTION'):
                item['llm_prompt_budget'] = dict(budget)
                changed = True
        if changed:
            bind.execute(
                workflows.update()
                .where(workflows.c.id == row['id'])
                .values(definition=definition, revision=row['revision'] + 1, updated_at=time.time_ns())
            )
    op.create_table(
        'experiment_prompt_bucket',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'session_task_id',
            sa.Text(),
            sa.ForeignKey('experiment_session_task.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('scope_key', sa.Text(), nullable=False),
        sa.Column('used', sa.Integer(), nullable=False),
        sa.Column('pending', sa.Integer(), nullable=False),
    )
    op.create_table(
        'experiment_prompt_reservation',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column(
            'bucket_id', sa.Text(), sa.ForeignKey('experiment_prompt_bucket.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('lease_expires_at', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_experiment_prompt_reservation_bucket_id', 'experiment_prompt_reservation', ['bucket_id'])


def downgrade():
    op.drop_table('experiment_prompt_reservation')
    op.drop_table('experiment_prompt_bucket')
    for name in ('experiment_session_task', 'experiment_plan_item'):
        with op.batch_alter_table(name) as batch:
            batch.drop_column('llm_prompt_budget')
    bind = op.get_bind()
    workflows = sa.table(
        'experiment_workflow',
        sa.column('id', sa.Text()),
        sa.column('definition', sa.JSON()),
        sa.column('revision', sa.Integer()),
        sa.column('updated_at', sa.BigInteger()),
    )
    for row in bind.execute(sa.select(workflows)).mappings().all():
        definition = row['definition']
        if isinstance(definition, str):
            definition = json.loads(definition)
        for item in definition.get('items', []):
            if isinstance(item, dict):
                item.pop('llm_prompt_budget', None)
        bind.execute(
            workflows.update()
            .where(workflows.c.id == row['id'])
            .values(definition=definition, revision=row['revision'] + 1, updated_at=time.time_ns())
        )
