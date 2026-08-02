"""Household mode: yours / mine / ours.

One household per user, joined via single-use expiring invite tokens. Sharing
is opt-in per account (`accounts.visibility = "household"`) and strictly
read-only: the shared view exposes names, types, and current balances of the
accounts other members chose to share — never their transactions, and never
any write path.
"""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import delete, select

from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import limiter
from app.models.account import Account
from app.models.household import Household, HouseholdInvite, HouseholdMember, HouseholdRole
from app.models.user import User
from app.schemas.household import (
    HouseholdCreate,
    HouseholdMemberOut,
    HouseholdOut,
    InviteOut,
    JoinRequest,
    SharedAccountOut,
    SharedCurrencyTotalOut,
    SharedViewOut,
)
from app.services.account_loader import load_account_data
from app.services.analytics.net_worth import current_balance

router = APIRouter(prefix="/household", tags=["household"])

INVITE_TTL_DAYS = 7


def _my_membership(db, user_id: int) -> HouseholdMember | None:
    return db.scalar(select(HouseholdMember).where(HouseholdMember.user_id == user_id))


def _household_out(db, household: Household, me_id: int, my_role: str) -> HouseholdOut:
    members = db.execute(
        select(HouseholdMember, User)
        .join(User, User.id == HouseholdMember.user_id)
        .where(HouseholdMember.household_id == household.id)
        .order_by(HouseholdMember.id)
    ).all()
    return HouseholdOut(
        id=household.id,
        name=household.name,
        my_role=my_role,
        members=[
            HouseholdMemberOut(
                user_id=u.id,
                email=u.email,
                full_name=u.full_name,
                role=m.role.value,
                is_me=u.id == me_id,
            )
            for m, u in members
        ],
    )


@router.get("", response_model=HouseholdOut | None)
def my_household(current: CurrentUser, db: DbSession) -> HouseholdOut | None:
    membership = _my_membership(db, current.id)
    if membership is None:
        return None
    return _household_out(db, membership.household, current.id, membership.role.value)


@router.post("", response_model=HouseholdOut, status_code=status.HTTP_201_CREATED)
def create_household(payload: HouseholdCreate, current: CurrentUser, db: DbSession) -> HouseholdOut:
    if _my_membership(db, current.id) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You already belong to a household")
    household = Household(name=payload.name.strip())
    db.add(household)
    db.flush()
    db.add(HouseholdMember(household_id=household.id, user_id=current.id, role=HouseholdRole.OWNER))
    db.commit()
    return _household_out(db, household, current.id, HouseholdRole.OWNER.value)


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
def create_invite(current: CurrentUser, db: DbSession) -> InviteOut:
    membership = _my_membership(db, current.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="You don't belong to a household")
    if membership.role != HouseholdRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can invite")
    invite = HouseholdInvite(
        household_id=membership.household_id,
        created_by=current.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    db.commit()
    return InviteOut(token=invite.token, expires_at=invite.expires_at)


@router.post("/join", response_model=HouseholdOut)
@limiter.limit("10/minute")
def join_household(request: Request, payload: JoinRequest, current: CurrentUser, db: DbSession) -> HouseholdOut:
    if _my_membership(db, current.id) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You already belong to a household")
    invite = db.scalar(select(HouseholdInvite).where(HouseholdInvite.token == payload.token))
    now = datetime.now(UTC)
    if invite is None or invite.used_at is not None or invite.expires_at < now:
        # One answer for unknown, used, and expired — no token oracle.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not valid")
    invite.used_by = current.id
    invite.used_at = now
    db.add(HouseholdMember(household_id=invite.household_id, user_id=current.id, role=HouseholdRole.MEMBER))
    db.commit()
    household = db.get(Household, invite.household_id)
    return _household_out(db, household, current.id, HouseholdRole.MEMBER.value)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(user_id: int, current: CurrentUser, db: DbSession) -> None:
    """Leave the household (yourself), or remove a member (owner only)."""
    membership = _my_membership(db, current.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="You don't belong to a household")
    if user_id != current.id and membership.role != HouseholdRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can remove members")
    target = db.scalar(
        select(HouseholdMember).where(
            HouseholdMember.household_id == membership.household_id,
            HouseholdMember.user_id == user_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not a member of your household")
    if target.role == HouseholdRole.OWNER and user_id != current.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The owner can't be removed")
    household_id = membership.household_id
    db.delete(target)
    db.flush()
    # Last one out turns off the lights — an empty household is meaningless.
    remaining = db.scalar(select(HouseholdMember).where(HouseholdMember.household_id == household_id))
    if remaining is None:
        db.execute(delete(Household).where(Household.id == household_id))
    elif target.role == HouseholdRole.OWNER:
        # The departing owner hands the household to the longest-standing member.
        oldest = db.scalar(
            select(HouseholdMember)
            .where(HouseholdMember.household_id == household_id)
            .order_by(HouseholdMember.id)
        )
        oldest.role = HouseholdRole.OWNER
    db.commit()


@router.get("/shared", response_model=SharedViewOut)
def shared_view(current: CurrentUser, db: DbSession) -> SharedViewOut:
    """Read-only: the household-visible accounts of every member (including
    your own, so the page shows what you're sharing). Balances only — no
    transactions, no categories, no mutations."""
    membership = _my_membership(db, current.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="You don't belong to a household")

    member_rows = db.execute(
        select(HouseholdMember.user_id, User)
        .join(User, User.id == HouseholdMember.user_id)
        .where(HouseholdMember.household_id == membership.household_id)
    ).all()
    users_by_id = {user_id: u for user_id, u in member_rows}

    accounts_out: list[SharedAccountOut] = []
    totals: dict[str, list] = {}
    for user_id, _u in member_rows:
        shared_ids = list(
            db.scalars(
                select(Account.id).where(Account.user_id == user_id, Account.visibility == "household")
            )
        )
        if not shared_ids:
            continue
        for acc in load_account_data(db, user_id, shared_ids):
            bal = current_balance(acc)
            owner = users_by_id[user_id]
            accounts_out.append(
                SharedAccountOut(
                    account_id=acc.id,
                    owner_user_id=user_id,
                    owner_name=owner.full_name or owner.email,
                    name=acc.name,
                    type=acc.type.value,
                    currency=acc.currency,
                    balance=bal.balance,
                    as_of=bal.as_of.isoformat() if bal.as_of else None,
                    source=bal.source,
                )
            )
            totals.setdefault(acc.currency, []).append(bal.balance)

    return SharedViewOut(
        household_id=membership.household_id,
        household_name=membership.household.name,
        accounts=accounts_out,
        totals=[
            SharedCurrencyTotalOut(currency=c, total=sum(vals))
            for c, vals in sorted(totals.items())
        ],
    )
