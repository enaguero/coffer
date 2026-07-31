from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.statement import StatementFormat, StatementImportStatus


class StatementImportOut(BaseModel):
    id: int
    account_id: int
    filename: str
    format: StatementFormat
    status: StatementImportStatus
    rows_parsed: int
    rows_imported: int

    model_config = {"from_attributes": True}


class ImportResponse(BaseModel):
    import_id: int
    rows_parsed: int
    rows_imported: int
    skipped_duplicates: int
    auto_categorized: int = 0


class PreviewRow(BaseModel):
    id: int  # opaque row index used in the confirm payload
    external_id: str | None
    posted_on: date
    description: str
    amount: Decimal
    suggested_category_id: int | None
    is_duplicate: bool


class PreviewResponse(BaseModel):
    import_id: int
    account_id: int
    filename: str
    rows: list[PreviewRow]
    duplicate_count: int
    auto_categorized_count: int
    # How the file was parsed: "ofx" | "qif" | "profile" | "preset:<bank_id>"
    # | "adapter:<key>" | "heuristic".
    source: str = "heuristic"
    warnings: list[str] = Field(default_factory=list)
    # ImportProfileConfig-shaped dict, present when the heuristic parsed the CSV
    # and its detected layout can be saved as this account's import profile.
    inferred_config: dict[str, Any] | None = None
    has_profile: bool = False


class ConfirmRow(BaseModel):
    id: int
    category_id: int | None = None
    skip: bool = False


class ConfirmRequest(BaseModel):
    rows: list[ConfirmRow]
