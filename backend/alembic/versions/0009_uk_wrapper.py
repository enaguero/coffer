"""accounts.uk_wrapper for tax-year allowance metering

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-31 18:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


uk_wrapper_enum = postgresql.ENUM("isa", "lisa", "pension", name="uk_wrapper", create_type=False)


def upgrade() -> None:
    uk_wrapper_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("accounts", sa.Column("uk_wrapper", uk_wrapper_enum, nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "uk_wrapper")
    uk_wrapper_enum.drop(op.get_bind(), checkfirst=True)
