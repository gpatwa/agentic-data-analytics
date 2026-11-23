"""A per-connection switch for warming the customer's warehouse before a user's first question (app.connect.warmup).

Off for every existing connection. Nothing here is a secret.

Revision ID: 0015_connect_warm_up
Revises: 0014_refusal_backlog
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_connect_warm_up"
down_revision = "0014_refusal_backlog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("connect_connections") as batch:
        batch.add_column(sa.Column("warm_up", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("connect_connections") as batch:
        batch.drop_column("warm_up")
