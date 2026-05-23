from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class StatementFormat(StrEnum):
    CSV = "csv"
    PDF = "pdf"


class StatementImportStatus(StrEnum):
    PREVIEW = "preview"
    COMMITTED = "committed"
    DISCARDED = "discarded"


class StatementImport(Base, TimestampMixin):
    __tablename__ = "statement_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    format: Mapped[StatementFormat] = mapped_column(
        Enum(StatementFormat, name="statement_format", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    status: Mapped[StatementImportStatus] = mapped_column(
        Enum(
            StatementImportStatus,
            name="statement_import_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=StatementImportStatus.COMMITTED,
    )
    rows_parsed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Set on previews, NULL after commit/discard. List of {id, external_id,
    # posted_on, description, amount, suggested_category_id, is_duplicate}.
    preview_rows: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000))

    user: Mapped["User"] = relationship(back_populates="statement_imports")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="statement_import")  # noqa: F821
