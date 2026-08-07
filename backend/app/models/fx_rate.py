from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Numeric(18, 8): 8 decimal places, 10 integer digits — the storage grid every
# rate must land on, whether entered manually or fetched from the feed.
RATE_QUANTUM = Decimal("0.00000001")
RATE_MAX = Decimal("9999999999")


class FxRate(Base, TimestampMixin):
    """Per-user exchange rate: 1 unit of `currency` = `rate` units of the
    user's display currency. Rates are entered manually by default; when the
    user opts in (`users.fx_auto_refresh`) they can also be fetched from an
    external provider (`services/fx_feed.py`). `source` records which — and
    manual entries always win: the feed never touches source="manual" rows."""

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("user_id", "currency", name="uq_fx_rate_user_currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    as_of: Mapped[date | None] = mapped_column(Date)
    # "manual" (user-entered) or "auto" (fetched by services/fx_feed.py).
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual", server_default="manual")
