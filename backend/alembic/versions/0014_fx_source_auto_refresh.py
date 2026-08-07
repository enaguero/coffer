"""fx_rates.source + users.fx_auto_refresh (opt-in FX feed)

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default="manual": every pre-existing rate was hand-entered.
    op.add_column("fx_rates", sa.Column("source", sa.String(10), nullable=False, server_default="manual"))
    # Auto-refresh is opt-in: everyone starts disabled, behavior unchanged.
    op.add_column("users", sa.Column("fx_auto_refresh", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "fx_auto_refresh")
    op.drop_column("fx_rates", "source")
