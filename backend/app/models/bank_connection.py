from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class BankProvider(StrEnum):
    GOCARDLESS = "gocardless"


class BankConnectionStatus(StrEnum):
    # Requisition created, user hasn't yet completed bank-side auth.
    PENDING = "pending"
    # User completed auth; we have account IDs and can fetch transactions.
    LINKED = "linked"
    # PSD2 90-day window has elapsed; user must re-authenticate.
    EXPIRED = "expired"
    # User (or we) revoked the requisition.
    REVOKED = "revoked"


class BankConnection(Base, TimestampMixin):
    """One row per linked institution per user.

    For GoCardless: `requisition_id`, `agreement_id`, and `institution_id` are opaque
    references — they cannot be used to access bank data without the app-level
    SECRET_ID/SECRET_KEY (which live only in env). So we do not encrypt them.
    """

    __tablename__ = "bank_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[BankProvider] = mapped_column(
        Enum(BankProvider, name="bank_provider", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    institution_id: Mapped[str] = mapped_column(String(120), nullable=False)
    institution_name: Mapped[str] = mapped_column(String(200), nullable=False)
    requisition_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    agreement_id: Mapped[str | None] = mapped_column(String(120))
    # Computed at link-time as `now + access_valid_for_days` from the EUA. PSD2
    # caps this at 180 days for transactions; we set 90 by default.
    requisition_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[BankConnectionStatus] = mapped_column(
        Enum(
            BankConnectionStatus,
            name="bank_connection_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=BankConnectionStatus.PENDING,
    )

    user: Mapped["User"] = relationship(back_populates="bank_connections")  # noqa: F821
    accounts: Mapped[list["Account"]] = relationship(back_populates="bank_connection")  # noqa: F821
    sync_jobs: Mapped[list["SyncJob"]] = relationship(back_populates="bank_connection", cascade="all, delete-orphan")  # noqa: F821
