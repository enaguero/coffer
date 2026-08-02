from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FxRate(Base, TimestampMixin):
    """User-maintained exchange rate: 1 unit of `currency` = `rate` units of
    the user's display currency. Self-hosted and offline by design — rates are
    entered manually (or via CSV import later), never fetched from an API."""

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("user_id", "currency", name="uq_fx_rate_user_currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    as_of: Mapped[date | None] = mapped_column(Date)
