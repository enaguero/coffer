"""goals: account linkage + monthly contribution

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-31 15:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "goals",
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("goals", sa.Column("monthly_contribution", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("goals", "monthly_contribution")
    op.drop_column("goals", "account_id")
