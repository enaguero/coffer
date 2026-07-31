from typing import Any

from pydantic import BaseModel, Field

from app.services.import_engine.profile import ImportProfileConfig


class ImportProfileUpsert(BaseModel):
    name: str = Field(default="Statement profile", min_length=1, max_length=120)
    # "custom", "inferred", or "preset:<bank_id>" — informational provenance.
    source: str = Field(default="custom", min_length=1, max_length=80)
    config: ImportProfileConfig


class ImportProfileOut(BaseModel):
    id: int
    account_id: int
    name: str
    source: str
    config: dict[str, Any]

    model_config = {"from_attributes": True}
