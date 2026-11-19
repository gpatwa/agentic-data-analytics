"""Durable Connect state: connection records, host claims and the setup audit chain.

Before this, the connection store and the audit chain of the Connect setups were process-local, so a restart forgot every
connection (and left its vault secret behind). Nothing here holds a secret: a record carries only a reference name into custody.

Revision ID: 0013_connect_store
Revises: 0012_durable_run_material
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_connect_store"
down_revision = "0012_durable_run_material"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connect_connections",
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("account", sa.String(length=255), nullable=False),
        sa.Column("user_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=255), nullable=False),
        sa.Column("warehouse", sa.String(length=255), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column("default_schema", sa.String(length=255), nullable=False),
        sa.Column("allowed_tables", sa.JSON(), nullable=False),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column("secret_name", sa.String(length=255), nullable=True),
        sa.Column("teardown", sa.JSON(), nullable=False),
        sa.Column("http_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("secret_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("connection_id"),
    )
    op.create_index("ix_connect_connections_tenant", "connect_connections", ["tenant_id"])
    op.create_index("ix_connect_connections_target", "connect_connections", ["engine", "host"])
    op.create_table(  # one owner per (engine, host): taken atomically by the primary key, so two tenants cannot both claim a target
        "connect_target_claims",
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("engine", "host"),
    )
    op.create_table(
        "connect_audit_events",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("event_hash"),
    )


def downgrade() -> None:
    for table in ("connect_audit_events", "connect_target_claims", "connect_connections"):
        op.drop_table(table)
