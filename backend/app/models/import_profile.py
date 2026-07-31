from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ImportProfile(Base, TimestampMixin):
    """Per-account statement-parsing configuration.

    One profile per account. `config` is a serialized
    `app.services.import_engine.profile.ImportProfileConfig` — validated at the
    API boundary, stored as JSONB so profile tweaks never need a migration.

    `source` records where the mapping came from: "custom" (user-edited),
    "inferred" (saved from a heuristic parse), or "preset:<bank_id>".
    """

    __tablename__ = "import_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Statement profile")
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="custom")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    account: Mapped["Account"] = relationship(back_populates="import_profile")  # noqa: F821
