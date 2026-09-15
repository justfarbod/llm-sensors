"""Preserve the origin of applied workflows without changing historical runs."""
import sqlalchemy as sa
from alembic import op

revision = 'f9a0b1c2d3e4'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [('source_workflow_id', sa.Text()), ('source_workflow_name', sa.Text()),
                       ('source_workflow_revision', sa.Integer()), ('workflow_origin', sa.Text()),
                       ('configuration_fingerprint', sa.Text())]:
        op.add_column('experiment_plan', sa.Column(name, kind, nullable=True))
    op.add_column('experiment_plan_item', sa.Column('source_step_key', sa.Text(), nullable=True))
    op.add_column('experiment_condition', sa.Column('source_condition_key', sa.Text(), nullable=True))
    op.create_index('ix_experiment_plan_workflow', 'experiment_plan', ['source_workflow_id', 'configuration_fingerprint'])


def downgrade():
    op.drop_index('ix_experiment_plan_workflow', table_name='experiment_plan')
    for table, names in [('experiment_plan_item', ['source_step_key']),
                         ('experiment_condition', ['source_condition_key']),
                         ('experiment_plan', ['source_workflow_id', 'source_workflow_name', 'source_workflow_revision',
                                              'workflow_origin', 'configuration_fingerprint'])]:
        with op.batch_alter_table(table) as batch:
            for name in names:
                batch.drop_column(name)
