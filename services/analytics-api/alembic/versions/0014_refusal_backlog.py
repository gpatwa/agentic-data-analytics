"""Refusal backlog: salted-hash counts of words behind coverage refusals, and a one-count-per-run guard.

No question text and no word is stored: only a keyed hash of each stem, a count and the refusal code.

Revision ID: 0014_refusal_backlog
Revises: 0013_connect_store
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_refusal_backlog"
down_revision = "0013_connect_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refusal_backlog_counts",
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("term_hash", sa.String(length=16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "day", "code", "term_hash"),
    )
    op.create_table(
        "refusal_backlog_seen",
        sa.Column("run_id", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("refusal_backlog_seen")
    op.drop_table("refusal_backlog_counts")
