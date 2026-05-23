from pydantic import BaseModel, Field


class CategoryRuleBase(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    category_id: int
    priority: int = 100


class CategoryRuleCreate(CategoryRuleBase):
    pass


class CategoryRuleUpdate(BaseModel):
    pattern: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: int | None = None
    priority: int | None = None


class CategoryRuleOut(CategoryRuleBase):
    id: int

    model_config = {"from_attributes": True}


class ApplyRulesResponse(BaseModel):
    rules_evaluated: int
    transactions_updated: int
