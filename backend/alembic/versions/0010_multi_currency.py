"""users.display_currency + fx_rates

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-02 16:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_currency", sa.String(3), nullable=True))
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "currency", name="uq_fx_rate_user_currency"),
    )
    op.create_index("ix_fx_rates_user_id", "fx_rates", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_fx_rates_user_id", table_name="fx_rates")
    op.drop_table("fx_rates")
    op.drop_column("users", "display_currency")
