from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account, AccountType
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.schemas.goal import GoalCreate, GoalOut, GoalUpdate
from app.services.account_loader import load_account_data
from app.services.analytics.goal_funding import compute_funding
from app.services.analytics.net_worth import current_balance

router = APIRouter(prefix="/goals", tags=["goals"])

# Liability accounts can't fund a goal — their balances run negative and their
# inflows are repayments, not savings.
FUNDABLE_TYPES = {AccountType.CHECKING, AccountType.SAVINGS, AccountType.CASH, AccountType.OTHER}


def _linked_balances(db, user_id: int, account_ids: set[int]) -> dict[int, Decimal]:
    """Best-known balance per linked account (snapshot-anchored, like net worth)."""
    return {acc.id: current_balance(acc).balance for acc in load_account_data(db, user_id, account_ids)}


def _funded_this_month(db, user_id: int, account_ids: set[int], today: date) -> dict[int, Decimal]:
    if not account_ids:
        return {}
    # Linked accounts with no contributions this month report £0, not "unknown".
    totals = {account_id: Decimal("0") for account_id in account_ids}
    rows = db.execute(
        select(Transaction.account_id, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.account_id.in_(account_ids),
            Transaction.amount > 0,
            Transaction.posted_on >= today.replace(day=1),
            Transaction.posted_on <= today,
        )
        .group_by(Transaction.account_id)
    ).all()
    totals.update(dict(rows))
    return totals


def _serialize(goal: Goal, balances: dict[int, Decimal], funded: dict[int, Decimal], today: date) -> GoalOut:
    target = Decimal(goal.target_amount or 0)
    auto_tracked = goal.account_id in balances
    current = balances[goal.account_id] if auto_tracked else Decimal(goal.current_amount or 0)

    funding = compute_funding(target, current, goal.target_date, goal.monthly_contribution, today)

    progress = float(current / target) if target > 0 else 0.0
    return GoalOut(
        id=goal.id,
        name=goal.name,
        target_amount=target,
        current_amount=current,
        target_date=goal.target_date,
        account_id=goal.account_id,
        monthly_contribution=goal.monthly_contribution,
        notes=goal.notes,
        progress=min(max(progress, 0.0), 1.0),
        auto_tracked=auto_tracked,
        required_monthly=funding.required_monthly,
        on_track=funding.on_track,
        funded_this_month=funded.get(goal.account_id),
        projected_date=funding.projected_date,
    )


def _serialize_all(db, user_id: int, goals: list[Goal]) -> list[GoalOut]:
    linked = {g.account_id for g in goals if g.account_id is not None}
    today = date.today()
    balances = _linked_balances(db, user_id, linked)
    funded = _funded_this_month(db, user_id, linked, today)
    return [_serialize(g, balances, funded, today) for g in goals]


def _check_linkable_account(db, current, account_id: int | None, goal_id: int | None = None) -> None:
    if account_id is None:
        return
    account = db.get(Account, account_id)
    if account is None or account.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    if account.type not in FUNDABLE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Goals can only be funded from asset accounts, not credit cards or loans",
        )
    # One pot funds one goal — two goals sharing an account would each claim
    # the full balance and double-count it.
    clash = db.scalar(select(Goal.id).where(Goal.account_id == account_id, Goal.id != (goal_id or 0)))
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That account already funds another goal",
        )


@router.get("", response_model=list[GoalOut])
def list_goals(current: CurrentUser, db: DbSession) -> list[GoalOut]:
    goals = list(db.scalars(select(Goal).where(Goal.user_id == current.id).order_by(Goal.name)))
    return _serialize_all(db, current.id, goals)


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(payload: GoalCreate, current: CurrentUser, db: DbSession) -> GoalOut:
    _check_linkable_account(db, current, payload.account_id)
    goal = Goal(user_id=current.id, **payload.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return _serialize_all(db, current.id, [goal])[0]


def _get_owned(db, current, goal_id: int) -> Goal:
    goal = db.get(Goal, goal_id)
    if goal is None or goal.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return goal


@router.patch("/{goal_id}", response_model=GoalOut)
def update_goal(goal_id: int, payload: GoalUpdate, current: CurrentUser, db: DbSession) -> GoalOut:
    goal = _get_owned(db, current, goal_id)
    data = payload.model_dump(exclude_unset=True)
    if "account_id" in data:
        _check_linkable_account(db, current, data["account_id"], goal_id=goal.id)
        # Unlinking: preserve the derived progress as the stored value so the
        # goal doesn't snap back to a stale (usually zero) current_amount.
        if data["account_id"] is None and goal.account_id is not None and "current_amount" not in data:
            balances = _linked_balances(db, current.id, {goal.account_id})
            if goal.account_id in balances:
                goal.current_amount = balances[goal.account_id]
    for key, value in data.items():
        setattr(goal, key, value)
    db.commit()
    db.refresh(goal)
    return _serialize_all(db, current.id, [goal])[0]


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(goal_id: int, current: CurrentUser, db: DbSession) -> None:
    goal = _get_owned(db, current, goal_id)
    db.delete(goal)
    db.commit()
