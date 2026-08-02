"""statement_imports: period + skipped rows for ground-truth checks

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("statement_imports", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("statement_imports", sa.Column("period_end", sa.Date(), nullable=True))
    op.add_column("statement_imports", sa.Column("skipped_external_ids", JSONB(), nullable=True))
    # Backfill periods for existing statements from their linked transactions —
    # coverage checks would otherwise report every pre-upgrade month as a gap.
    op.execute(
        """
        UPDATE statement_imports s
        SET period_start = t.min_on, period_end = t.max_on
        FROM (
            SELECT statement_import_id, MIN(posted_on) AS min_on, MAX(posted_on) AS max_on
            FROM transactions
            WHERE statement_import_id IS NOT NULL
            GROUP BY statement_import_id
        ) t
        WHERE t.statement_import_id = s.id
        """
    )


def downgrade() -> None:
    op.drop_column("statement_imports", "skipped_external_ids")
    op.drop_column("statement_imports", "period_end")
    op.drop_column("statement_imports", "period_start")
