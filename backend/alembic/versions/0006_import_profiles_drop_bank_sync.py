"""import_profiles + accounts.bank_id; drop bank_connections/sync_jobs

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-30 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


bank_provider_enum = postgresql.ENUM("gocardless", name="bank_provider", create_type=False)
bank_connection_status_enum = postgresql.ENUM(
    "pending", "linked", "expired", "revoked",
    name="bank_connection_status",
    create_type=False,
)
sync_job_status_enum = postgresql.ENUM(
    "running", "success", "failed", name="sync_job_status", create_type=False
)


def upgrade() -> None:
    # --- Remove the provider-sync (GoCardless) schema -------------------------
    op.drop_index("ix_sync_jobs_bank_connection_id", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_user_id", table_name="sync_jobs")
    op.drop_table("sync_jobs")

    op.drop_index("ix_accounts_bank_connection_id", table_name="accounts")
    op.drop_column("accounts", "external_account_id")
    op.drop_column("accounts", "bank_connection_id")

    op.drop_index("ix_bank_connections_requisition_id", table_name="bank_connections")
    op.drop_index("ix_bank_connections_user_id", table_name="bank_connections")
    op.drop_table("bank_connections")

    bind = op.get_bind()
    sync_job_status_enum.drop(bind, checkfirst=True)
    bank_connection_status_enum.drop(bind, checkfirst=True)
    bank_provider_enum.drop(bind, checkfirst=True)

    # --- Statement-import replacements ---------------------------------------
    # Slug into the UK bank catalog; drives import preset selection.
    op.add_column("accounts", sa.Column("bank_id", sa.String(50), nullable=True))

    op.create_table(
        "import_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_import_profiles_user_id", "import_profiles", ["user_id"])

    # New self-describing statement formats. Safe inside a transaction on PG 12+
    # as long as this migration doesn't itself insert rows using the new values.
    op.execute("ALTER TYPE statement_format ADD VALUE IF NOT EXISTS 'ofx'")
    op.execute("ALTER TYPE statement_format ADD VALUE IF NOT EXISTS 'qif'")


def downgrade() -> None:
    op.drop_index("ix_import_profiles_user_id", table_name="import_profiles")
    op.drop_table("import_profiles")
    op.drop_column("accounts", "bank_id")
    # Postgres can't remove enum values; 'ofx'/'qif' stay on statement_format.
    # The bank_connections/sync_jobs schema is intentionally not restored.
