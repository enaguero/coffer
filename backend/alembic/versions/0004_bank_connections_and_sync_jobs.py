"""bank_connections + sync_jobs + accounts linkage

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-22 14:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


bank_provider_enum = postgresql.ENUM(
    "gocardless", name="bank_provider", create_type=False
)
bank_connection_status_enum = postgresql.ENUM(
    "pending", "linked", "expired", "revoked",
    name="bank_connection_status",
    create_type=False,
)
sync_job_status_enum = postgresql.ENUM(
    "running", "success", "failed",
    name="sync_job_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    bank_provider_enum.create(bind, checkfirst=True)
    bank_connection_status_enum.create(bind, checkfirst=True)
    sync_job_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "bank_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", bank_provider_enum, nullable=False),
        sa.Column("institution_id", sa.String(120), nullable=False),
        sa.Column("institution_name", sa.String(200), nullable=False),
        sa.Column("requisition_id", sa.String(120), nullable=False),
        sa.Column("agreement_id", sa.String(120)),
        sa.Column("requisition_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "status",
            bank_connection_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_bank_connections_user_id", "bank_connections", ["user_id"])
    op.create_index(
        "ix_bank_connections_requisition_id", "bank_connections", ["requisition_id"]
    )

    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "bank_connection_id",
            sa.Integer(),
            sa.ForeignKey("bank_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sync_job_status_enum, nullable=False, server_default="running"),
        sa.Column("transactions_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transactions_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sync_jobs_user_id", "sync_jobs", ["user_id"])
    op.create_index("ix_sync_jobs_bank_connection_id", "sync_jobs", ["bank_connection_id"])

    op.add_column(
        "accounts",
        sa.Column(
            "bank_connection_id",
            sa.Integer(),
            sa.ForeignKey("bank_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "accounts",
        sa.Column("external_account_id", sa.String(120), nullable=True),
    )
    op.create_index(
        "ix_accounts_bank_connection_id", "accounts", ["bank_connection_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_accounts_bank_connection_id", table_name="accounts")
    op.drop_column("accounts", "external_account_id")
    op.drop_column("accounts", "bank_connection_id")

    op.drop_index("ix_sync_jobs_bank_connection_id", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_user_id", table_name="sync_jobs")
    op.drop_table("sync_jobs")

    op.drop_index("ix_bank_connections_requisition_id", table_name="bank_connections")
    op.drop_index("ix_bank_connections_user_id", table_name="bank_connections")
    op.drop_table("bank_connections")

    sync_job_status_enum.drop(op.get_bind(), checkfirst=True)
    bank_connection_status_enum.drop(op.get_bind(), checkfirst=True)
    bank_provider_enum.drop(op.get_bind(), checkfirst=True)
