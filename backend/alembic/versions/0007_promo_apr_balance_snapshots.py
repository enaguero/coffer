"""debts promo-APR window + balance_snapshots

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-31 10:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


balance_source_enum = postgresql.ENUM("statement", "manual", name="balance_source", create_type=False)


def upgrade() -> None:
    op.add_column("debts", sa.Column("promo_apr", sa.Numeric(6, 3), nullable=True))
    op.add_column("debts", sa.Column("promo_ends_on", sa.Date(), nullable=True))

    op.add_column("statement_imports", sa.Column("closing_balance", sa.Numeric(14, 2), nullable=True))
    op.add_column("statement_imports", sa.Column("closing_balance_date", sa.Date(), nullable=True))

    balance_source_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "balance_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("balance", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", balance_source_enum, nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "as_of", name="uq_balance_snapshot_account_date"),
    )
    op.create_index("ix_balance_snapshots_user_id", "balance_snapshots", ["user_id"])
    op.create_index("ix_balance_snapshots_account_id", "balance_snapshots", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_balance_snapshots_account_id", table_name="balance_snapshots")
    op.drop_index("ix_balance_snapshots_user_id", table_name="balance_snapshots")
    op.drop_table("balance_snapshots")
    balance_source_enum.drop(op.get_bind(), checkfirst=True)
    op.drop_column("statement_imports", "closing_balance_date")
    op.drop_column("statement_imports", "closing_balance")
    op.drop_column("debts", "promo_ends_on")
    op.drop_column("debts", "promo_apr")
