from pydantic import BaseModel, Field

from app.models.category import CategoryKind


class CategoryBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: CategoryKind = CategoryKind.EXPENSE
    color: str | None = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: str | None = None
    kind: CategoryKind | None = None
    color: str | None = None


class CategoryOut(CategoryBase):
    id: int

    model_config = {"from_attributes": True}
