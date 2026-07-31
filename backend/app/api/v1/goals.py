from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.schemas.goal import GoalCreate, GoalOut, GoalUpdate
from app.services.analytics.net_worth import current_balance

router = APIRouter(prefix="/goals", tags=["goals"])

DAYS_PER_MONTH = Decimal("30.44")


def _linked_balances(db, user_id: int, account_ids: set[int]) -> dict[int, Decimal]:
    """Best-known balance per linked account (snapshot-anchored, like net worth)."""
    if not account_ids:
        return {}
    from app.api.v1.insights import _account_data

    return {acc.id: current_balance(acc).balance for acc in _account_data(db, user_id) if acc.id in account_ids}


def _funded_this_month(db, user_id: int, account_ids: set[int], today: date) -> dict[int, Decimal]:
    if not account_ids:
        return {}
    # Linked accounts with no contributions this month report £0, not "unknown".
    totals = {account_id: Decimal("0") for account_id in account_ids}
    rows = db.execute(
        select(Transaction.account_id, func.coalesce(func.sum(Transaction.amount), 0))
        .where(
            Transaction.user_id == user_id,
            Transaction.account_id.in_(account_ids),
            Transaction.amount > 0,
            Transaction.posted_on >= today.replace(day=1),
            Transaction.posted_on <= today,
        )
        .group_by(Transaction.account_id)
    ).all()
    totals.update({account_id: Decimal(total) for account_id, total in rows})
    return totals


def _serialize(
    goal: Goal,
    balances: dict[int, Decimal],
    funded: dict[int, Decimal],
    today: date | None = None,
) -> GoalOut:
    today = today or date.today()
    target = Decimal(goal.target_amount or 0)

    auto_tracked = goal.account_id is not None and goal.account_id in balances
    current = balances[goal.account_id] if auto_tracked else Decimal(goal.current_amount or 0)
    remaining = target - current

    required_monthly: Decimal | None = None
    if remaining > 0 and goal.target_date is not None and goal.target_date > today:
        months_left = Decimal((goal.target_date - today).days) / DAYS_PER_MONTH
        if months_left > 0:
            required_monthly = (remaining / months_left).quantize(Decimal("0.01"))

    on_track: bool | None = None
    if remaining <= 0:
        on_track = True
    elif required_monthly is not None and goal.monthly_contribution is not None:
        on_track = goal.monthly_contribution >= required_monthly

    projected_date: date | None = None
    if remaining > 0 and goal.monthly_contribution and goal.monthly_contribution > 0:
        months = (remaining / goal.monthly_contribution).quantize(Decimal("1"), rounding=ROUND_CEILING)
        projected_date = today + timedelta(days=int(months * DAYS_PER_MONTH))

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
        required_monthly=required_monthly,
        on_track=on_track,
        funded_this_month=funded.get(goal.account_id) if goal.account_id else None,
        projected_date=projected_date,
    )


def _serialize_all(db, user_id: int, goals: list[Goal]) -> list[GoalOut]:
    linked = {g.account_id for g in goals if g.account_id is not None}
    balances = _linked_balances(db, user_id, linked)
    funded = _funded_this_month(db, user_id, linked, date.today())
    return [_serialize(g, balances, funded) for g in goals]


def _check_account_owned(db, current, account_id: int | None) -> None:
    if account_id is None:
        return
    account = db.get(Account, account_id)
    if account is None or account.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")


@router.get("", response_model=list[GoalOut])
def list_goals(current: CurrentUser, db: DbSession) -> list[GoalOut]:
    goals = list(db.scalars(select(Goal).where(Goal.user_id == current.id).order_by(Goal.name)))
    return _serialize_all(db, current.id, goals)


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(payload: GoalCreate, current: CurrentUser, db: DbSession) -> GoalOut:
    _check_account_owned(db, current, payload.account_id)
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
        _check_account_owned(db, current, data["account_id"])
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
