from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
def list_categories(current: CurrentUser, db: DbSession) -> list[Category]:
    return list(
        db.scalars(select(Category).where(Category.user_id == current.id).order_by(Category.name))
    )


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreate, current: CurrentUser, db: DbSession) -> Category:
    category = Category(user_id=current.id, **payload.model_dump())
    db.add(category)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Category name already used") from e
    db.refresh(category)
    return category


def _get_owned(db, current, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None or category.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int, payload: CategoryUpdate, current: CurrentUser, db: DbSession
) -> Category:
    category = _get_owned(db, current, category_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, key, value)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, current: CurrentUser, db: DbSession) -> None:
    category = _get_owned(db, current, category_id)
    db.delete(category)
    db.commit()
