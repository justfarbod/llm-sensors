"""repair experiment warning runtime state columns

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9

Some databases applied the experiment-perturbation migration before
``last_token`` and ``pending_trigger_key`` were included in that revision.
Those databases report 500 errors from the experiment runtime and chat
endpoints as soon as an enabled warning tries to create its runtime state.
"""

import sqlalchemy as sa
from alembic import op


revision = 'b5c6d7e8f9a0'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('experiment_warning_state'):
        return

    columns = {column['name'] for column in inspector.get_columns('experiment_warning_state')}
    missing_columns = [
        sa.Column('last_token', sa.Text(), nullable=True),
        sa.Column('pending_trigger_key', sa.Text(), nullable=True),
    ]

    for column in missing_columns:
        if column.name not in columns:
            op.add_column('experiment_warning_state', column)


def downgrade():
    # This is a repair for columns owned by the earlier perturbation migration.
    # Leaving them in place makes downgrade safe for both repaired databases and
    # fresh databases where the columns already existed before this revision.
    pass
