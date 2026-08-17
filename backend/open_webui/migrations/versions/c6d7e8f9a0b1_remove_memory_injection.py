"""remove memory injection from experiment conditions

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""

import json
import time

import sqlalchemy as sa
from alembic import op


revision = 'c6d7e8f9a0b1'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None


def _definition(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _rewrite_workflows(remove_memory):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('experiment_workflow'):
        return
    workflows = sa.table(
        'experiment_workflow',
        sa.column('id', sa.Text()),
        sa.column('definition', sa.JSON()),
        sa.column('revision', sa.Integer()),
        sa.column('updated_at', sa.BigInteger()),
    )
    now = time.time_ns()
    for row in bind.execute(
        sa.select(workflows.c.id, workflows.c.definition, workflows.c.revision)
    ).mappings():
        definition = _definition(row['definition'])
        if definition is None or not isinstance(definition.get('conditions'), list):
            continue
        changed = False
        for condition in definition['conditions']:
            if not isinstance(condition, dict):
                continue
            if remove_memory:
                changed = condition.pop('memory_injection', None) is not None or changed
            elif 'memory_injection' not in condition:
                condition['memory_injection'] = {
                    'enabled': False,
                    'content': '',
                    'persist_for_session': False,
                    'activation': {
                        'mode': 'EVERY_REQUEST',
                        'count': None,
                        'range_start': None,
                        'range_end': None,
                        'probability': 1,
                        'scope': 'ALL_TASKS',
                        'plan_item_ids': [],
                    },
                }
                changed = True
        if changed:
            bind.execute(
                sa.update(workflows)
                .where(workflows.c.id == row['id'])
                .values(
                    definition=definition,
                    revision=(row['revision'] or 0) + 1,
                    updated_at=now,
                )
            )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _rewrite_workflows(remove_memory=True)

    if inspector.has_table('experiment_condition_task_scope'):
        bind.execute(
            sa.text(
                "DELETE FROM experiment_condition_task_scope WHERE perturbation_type = 'MEMORY'"
            )
        )
    if inspector.has_table('experiment_memory_injection'):
        op.drop_table('experiment_memory_injection')

    request_columns = {
        column['name']
        for column in inspector.get_columns('experiment_llm_request')
    }
    with op.batch_alter_table('experiment_llm_request') as batch_op:
        for name in (
            'memory_active',
            'memory_latched',
            'memory_draw',
            'memory_randomization_id',
        ):
            if name in request_columns:
                batch_op.drop_column(name)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    request_columns = {
        column['name']
        for column in inspector.get_columns('experiment_llm_request')
    }
    with op.batch_alter_table('experiment_llm_request') as batch_op:
        if 'memory_active' not in request_columns:
            batch_op.add_column(
                sa.Column(
                    'memory_active',
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if 'memory_latched' not in request_columns:
            batch_op.add_column(
                sa.Column(
                    'memory_latched',
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if 'memory_draw' not in request_columns:
            batch_op.add_column(sa.Column('memory_draw', sa.Float(), nullable=True))
        if 'memory_randomization_id' not in request_columns:
            batch_op.add_column(
                sa.Column('memory_randomization_id', sa.Text(), nullable=True)
            )

    inspector = sa.inspect(bind)
    if not inspector.has_table('experiment_memory_injection'):
        op.create_table(
            'experiment_memory_injection',
            sa.Column(
                'condition_id',
                sa.Text(),
                sa.ForeignKey('experiment_condition.id', ondelete='CASCADE'),
                primary_key=True,
            ),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('content', sa.Text(), nullable=False, server_default=''),
            sa.Column(
                'persist_for_session',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                'activation_mode',
                sa.Text(),
                nullable=False,
                server_default='EVERY_REQUEST',
            ),
            sa.Column('activation_count', sa.Integer(), nullable=True),
            sa.Column('range_start', sa.Integer(), nullable=True),
            sa.Column('range_end', sa.Integer(), nullable=True),
            sa.Column('probability', sa.Float(), nullable=False, server_default='1'),
            sa.Column('scope', sa.Text(), nullable=False, server_default='ALL_TASKS'),
        )
        bind.execute(
            sa.text(
                "INSERT INTO experiment_memory_injection "
                "(condition_id, enabled, content, persist_for_session, activation_mode, probability, scope) "
                "SELECT id, :enabled, '', :persist, 'EVERY_REQUEST', 1, 'ALL_TASKS' "
                'FROM experiment_condition'
            ),
            {'enabled': False, 'persist': False},
        )
    _rewrite_workflows(remove_memory=False)
