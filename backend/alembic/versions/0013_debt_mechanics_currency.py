"""debts: repayment_type + currency + installment_amount

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    repayment_type = sa.Enum("revolving", "amortized", "flat", "statement_only", name="debt_repayment_type")
    repayment_type.create(op.get_bind(), checkfirst=True)
    # server_default="revolving": existing debts migrate to the type whose math
    # matches today's engine exactly — zero behavior change until edited.
    op.add_column(
        "debts",
        sa.Column("repayment_type", repayment_type, nullable=False, server_default="revolving"),
    )
    # NULL currency = the user's display currency (the pre-existing register
    # convention); no backfill guess.
    op.add_column("debts", sa.Column("currency", sa.String(3), nullable=True))
    op.add_column("debts", sa.Column("installment_amount", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("debts", "installment_amount")
    op.drop_column("debts", "currency")
    op.drop_column("debts", "repayment_type")
    sa.Enum(name="debt_repayment_type").drop(op.get_bind(), checkfirst=True)
