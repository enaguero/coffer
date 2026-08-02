"""Household mode: yours / mine / ours.

A user belongs to at most one household. Members choose per account whether it
is visible to the household (`accounts.visibility = "household"`); everything
else stays private. Shared visibility is strictly read-only — the household
never grants write access to another member's data.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class HouseholdRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class Household(Base, TimestampMixin):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    members: Mapped[list["HouseholdMember"]] = relationship(back_populates="household", cascade="all, delete-orphan")  # noqa: E501
    invites: Mapped[list["HouseholdInvite"]] = relationship(back_populates="household", cascade="all, delete-orphan")  # noqa: E501


class HouseholdMember(Base, TimestampMixin):
    __tablename__ = "household_members"
    # One household per user — "yours/mine/ours", not a sharing graph.
    __table_args__ = (UniqueConstraint("user_id", name="uq_household_member_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[HouseholdRole] = mapped_column(
        Enum(HouseholdRole, name="household_role", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=HouseholdRole.MEMBER,
    )

    household: Mapped["Household"] = relationship(back_populates="members")


class HouseholdInvite(Base, TimestampMixin):
    __tablename__ = "household_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Single-use, expiring bearer token (secrets.token_urlsafe).
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    household: Mapped["Household"] = relationship(back_populates="invites")
