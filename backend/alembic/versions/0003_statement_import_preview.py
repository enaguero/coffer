"""statement_import preview flow

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22 13:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


status_enum = postgresql.ENUM(
    "preview", "committed", "discarded",
    name="statement_import_status",
    create_type=False,
)


def upgrade() -> None:
    status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "statement_imports",
        sa.Column(
            "status",
            sa.Enum(
                "preview", "committed", "discarded",
                name="statement_import_status",
                create_type=False,
            ),
            nullable=False,
            server_default="committed",
        ),
    )
    op.add_column(
        "statement_imports",
        sa.Column("preview_rows", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("statement_imports", "preview_rows")
    op.drop_column("statement_imports", "status")
    status_enum.drop(op.get_bind(), checkfirst=True)
