"""cashflow_lines + cashflow_entries

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


cashflow_kind_enum = postgresql.ENUM(
    "income", "expense", name="cashflow_kind", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    cashflow_kind_enum.create(bind, checkfirst=True)

    op.create_table(
        "cashflow_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", cashflow_kind_enum, nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_cashflow_line_user_name"),
    )
    op.create_index("ix_cashflow_lines_user_id", "cashflow_lines", ["user_id"])
    op.create_index("ix_cashflow_lines_country", "cashflow_lines", ["country"])

    op.create_table(
        "cashflow_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "line_id",
            sa.Integer(),
            sa.ForeignKey("cashflow_lines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("line_id", "year", "month", name="uq_cashflow_entry_line_period"),
    )
    op.create_index("ix_cashflow_entries_user_id", "cashflow_entries", ["user_id"])
    op.create_index("ix_cashflow_entries_line_id", "cashflow_entries", ["line_id"])


def downgrade() -> None:
    op.drop_index("ix_cashflow_entries_line_id", table_name="cashflow_entries")
    op.drop_index("ix_cashflow_entries_user_id", table_name="cashflow_entries")
    op.drop_table("cashflow_entries")

    op.drop_index("ix_cashflow_lines_country", table_name="cashflow_lines")
    op.drop_index("ix_cashflow_lines_user_id", table_name="cashflow_lines")
    op.drop_table("cashflow_lines")

    cashflow_kind_enum.drop(op.get_bind(), checkfirst=True)
