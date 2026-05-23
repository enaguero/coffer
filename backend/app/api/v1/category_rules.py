from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.models.category import Category
from app.models.category_rule import CategoryRule
from app.models.transaction import Transaction
from app.schemas.category_rule import (
    ApplyRulesResponse,
    CategoryRuleCreate,
    CategoryRuleOut,
    CategoryRuleUpdate,
)
from app.services.categorization import compile_rules, match_category

router = APIRouter(prefix="/category-rules", tags=["category-rules"])


def _user_rules(db, user_id: int) -> list[CategoryRule]:
    return list(
        db.scalars(
            select(CategoryRule)
            .where(CategoryRule.user_id == user_id)
            .order_by(CategoryRule.priority, CategoryRule.id)
        )
    )


@router.get("", response_model=list[CategoryRuleOut])
def list_rules(current: CurrentUser, db: DbSession) -> list[CategoryRule]:
    return _user_rules(db, current.id)


@router.post("", response_model=CategoryRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: CategoryRuleCreate, current: CurrentUser, db: DbSession
) -> CategoryRule:
    category = db.get(Category, payload.category_id)
    if category is None or category.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    rule = CategoryRule(user_id=current.id, **payload.model_dump())
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Rule with that pattern already exists"
        ) from e
    db.refresh(rule)
    return rule


def _get_owned(db, current, rule_id: int) -> CategoryRule:
    rule = db.get(CategoryRule, rule_id)
    if rule is None or rule.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


@router.patch("/{rule_id}", response_model=CategoryRuleOut)
def update_rule(
    rule_id: int, payload: CategoryRuleUpdate, current: CurrentUser, db: DbSession
) -> CategoryRule:
    rule = _get_owned(db, current, rule_id)
    if payload.category_id is not None:
        cat = db.get(Category, payload.category_id)
        if cat is None or cat.user_id != current.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, current: CurrentUser, db: DbSession) -> None:
    rule = _get_owned(db, current, rule_id)
    db.delete(rule)
    db.commit()


@router.post("/apply", response_model=ApplyRulesResponse)
def apply_rules_to_uncategorized(
    current: CurrentUser, db: DbSession
) -> ApplyRulesResponse:
    """Run current rules against every uncategorized transaction. One-off
    catch-up for rules added after the imports they should have caught."""
    rules = _user_rules(db, current.id)
    compiled = compile_rules(rules)
    if not compiled:
        return ApplyRulesResponse(rules_evaluated=0, transactions_updated=0)

    uncategorized = list(
        db.scalars(
            select(Transaction).where(
                Transaction.user_id == current.id,
                Transaction.category_id.is_(None),
            )
        )
    )
    updated = 0
    for txn in uncategorized:
        match = match_category(txn.description, compiled)
        if match is not None:
            txn.category_id = match
            updated += 1
    if updated:
        db.commit()
    return ApplyRulesResponse(rules_evaluated=len(rules), transactions_updated=updated)
