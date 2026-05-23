from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CategoryRule(Base, TimestampMixin):
    """If `pattern` (case-insensitive substring) appears in a transaction's
    description, assign `category_id` during import. Lowest `priority` wins
    when multiple rules match — ties broken by id for stable ordering.
    """

    __tablename__ = "category_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "pattern", name="uq_category_rule_user_pattern"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    user: Mapped["User"] = relationship(back_populates="category_rules")  # noqa: F821
    category: Mapped["Category"] = relationship(back_populates="rules")  # noqa: F821
