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
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import limiter
from app.models.account import Account, AccountVisibility
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


def _reset_shared_accounts(db, user_id: int) -> None:
    """Sharing consent belongs to ONE household: joining or leaving resets the
    user's household-visible accounts to private, so a flag granted to a
    previous household can never silently expose balances to the next one."""
    db.execute(
        update(Account)
        .where(Account.user_id == user_id, Account.visibility == AccountVisibility.HOUSEHOLD)
        .values(visibility=AccountVisibility.PRIVATE)
    )


def _revoke_unused_invites(db, household_id: int) -> None:
    """Membership changed — outstanding invites were minted under a different
    roster and can no longer be vouched for."""
    db.execute(
        delete(HouseholdInvite).where(
            HouseholdInvite.household_id == household_id, HouseholdInvite.used_at.is_(None)
        )
    )


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
    household = Household(name=payload.name)
    db.add(household)
    db.flush()
    db.add(HouseholdMember(household_id=household.id, user_id=current.id, role=HouseholdRole.OWNER))
    try:
        db.commit()
    except IntegrityError:
        # A concurrent create/join won the race for this user's one membership.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You already belong to a household"
        ) from None
    return _household_out(db, household, current.id, HouseholdRole.OWNER.value)


def _owner_membership(db, user_id: int) -> HouseholdMember:
    membership = _my_membership(db, user_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="You don't belong to a household")
    if membership.role != HouseholdRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner can manage invites")
    return membership


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
def create_invite(request: Request, current: CurrentUser, db: DbSession) -> InviteOut:
    membership = _owner_membership(db, current.id)
    # Expired invites are dead weight — prune them whenever a new one is cut.
    db.execute(
        delete(HouseholdInvite).where(
            HouseholdInvite.household_id == membership.household_id,
            HouseholdInvite.used_at.is_(None),
            HouseholdInvite.expires_at < datetime.now(UTC),
        )
    )
    invite = HouseholdInvite(
        household_id=membership.household_id,
        created_by=current.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    db.commit()
    return InviteOut(id=invite.id, token=invite.token, expires_at=invite.expires_at)


@router.get("/invites", response_model=list[InviteOut])
def list_invites(current: CurrentUser, db: DbSession) -> list[InviteOut]:
    """Outstanding (unused, unexpired) invites — so a leaked token is at
    least visible and revocable."""
    membership = _owner_membership(db, current.id)
    invites = db.scalars(
        select(HouseholdInvite).where(
            HouseholdInvite.household_id == membership.household_id,
            HouseholdInvite.used_at.is_(None),
            HouseholdInvite.expires_at >= datetime.now(UTC),
        )
    )
    return [InviteOut(id=i.id, token=i.token, expires_at=i.expires_at) for i in invites]


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(invite_id: int, current: CurrentUser, db: DbSession) -> None:
    membership = _owner_membership(db, current.id)
    invite = db.scalar(
        select(HouseholdInvite).where(
            HouseholdInvite.id == invite_id,
            HouseholdInvite.household_id == membership.household_id,
            HouseholdInvite.used_at.is_(None),
        )
    )
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    db.delete(invite)
    db.commit()


@router.post("/join", response_model=HouseholdOut)
@limiter.limit("10/minute")
def join_household(request: Request, payload: JoinRequest, current: CurrentUser, db: DbSession) -> HouseholdOut:
    if _my_membership(db, current.id) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You already belong to a household")
    now = datetime.now(UTC)
    # Atomic claim: the conditional UPDATE is the single-use guarantee — two
    # concurrent redemptions can't both see "unused" and both join.
    claimed = db.execute(
        update(HouseholdInvite)
        .where(
            HouseholdInvite.token == payload.token,
            HouseholdInvite.used_at.is_(None),
            HouseholdInvite.expires_at >= now,
        )
        .values(used_by=current.id, used_at=now)
        .returning(HouseholdInvite.household_id)
    ).first()
    if claimed is None:
        # One answer for unknown, used, and expired — no token oracle.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not valid")
    household_id = claimed[0]
    # Joining a new household never inherits sharing consent granted to a
    # previous one.
    _reset_shared_accounts(db, current.id)
    db.add(HouseholdMember(household_id=household_id, user_id=current.id, role=HouseholdRole.MEMBER))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You already belong to a household"
        ) from None
    household = db.get(Household, household_id)
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
    # Departure revokes the leaver's sharing consent and every outstanding
    # invite — the roster they were minted under no longer exists.
    _reset_shared_accounts(db, user_id)
    _revoke_unused_invites(db, household_id)
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
