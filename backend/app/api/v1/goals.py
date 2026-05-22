from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models.goal import Goal
from app.schemas.goal import GoalCreate, GoalOut, GoalUpdate

router = APIRouter(prefix="/goals", tags=["goals"])


def _serialize(goal: Goal) -> GoalOut:
    target = Decimal(goal.target_amount or 0)
    current = Decimal(goal.current_amount or 0)
    progress = float(current / target) if target > 0 else 0.0
    return GoalOut(
        id=goal.id,
        name=goal.name,
        target_amount=target,
        current_amount=current,
        target_date=goal.target_date,
        notes=goal.notes,
        progress=min(max(progress, 0.0), 1.0),
    )


@router.get("", response_model=list[GoalOut])
def list_goals(current: CurrentUser, db: DbSession) -> list[GoalOut]:
    goals = db.scalars(select(Goal).where(Goal.user_id == current.id).order_by(Goal.name))
    return [_serialize(g) for g in goals]


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(payload: GoalCreate, current: CurrentUser, db: DbSession) -> GoalOut:
    goal = Goal(user_id=current.id, **payload.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return _serialize(goal)


def _get_owned(db, current, goal_id: int) -> Goal:
    goal = db.get(Goal, goal_id)
    if goal is None or goal.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return goal


@router.patch("/{goal_id}", response_model=GoalOut)
def update_goal(goal_id: int, payload: GoalUpdate, current: CurrentUser, db: DbSession) -> GoalOut:
    goal = _get_owned(db, current, goal_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(goal, key, value)
    db.commit()
    db.refresh(goal)
    return _serialize(goal)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(goal_id: int, current: CurrentUser, db: DbSession) -> None:
    goal = _get_owned(db, current, goal_id)
    db.delete(goal)
    db.commit()
