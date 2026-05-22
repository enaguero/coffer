from pydantic import BaseModel

from app.models.statement import StatementFormat


class StatementImportOut(BaseModel):
    id: int
    account_id: int
    filename: str
    format: StatementFormat
    rows_parsed: int
    rows_imported: int

    model_config = {"from_attributes": True}


class ImportResponse(BaseModel):
    import_id: int
    rows_parsed: int
    rows_imported: int
    skipped_duplicates: int
