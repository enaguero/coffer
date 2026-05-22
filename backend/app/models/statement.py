from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class StatementFormat(StrEnum):
    CSV = "csv"
    PDF = "pdf"


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
    rows_parsed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000))

    user: Mapped["User"] = relationship(back_populates="statement_imports")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="statement_import")  # noqa: F821
