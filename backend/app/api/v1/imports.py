import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.statement import StatementFormat, StatementImport
from app.models.transaction import Transaction
from app.schemas.statement import ImportResponse, StatementImportOut
from app.services.csv_parser import parse_csv
from app.services.pdf_parser import parse_pdf

router = APIRouter(prefix="/imports", tags=["imports"])

ALLOWED_EXTENSIONS = {".csv", ".pdf"}


@router.get("", response_model=list[StatementImportOut])
def list_imports(current: CurrentUser, db: DbSession) -> list[StatementImport]:
    return list(
        db.scalars(
            select(StatementImport)
            .where(StatementImport.user_id == current.id)
            .order_by(StatementImport.created_at.desc())
        )
    )


@router.post("/upload", response_model=ImportResponse, status_code=status.HTTP_201_CREATED)
async def upload_statement(
    current: CurrentUser,
    db: DbSession,
    account_id: int = Form(...),
    file: UploadFile = File(...),
) -> ImportResponse:
    account = db.get(Account, account_id)
    if account is None or account.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {suffix}. Use .csv or .pdf",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    upload_dir = Path(settings.upload_dir) / str(current.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(content)

    if suffix == ".csv":
        rows, _skipped = parse_csv(content)
        fmt = StatementFormat.CSV
    else:
        rows, _skipped = parse_pdf(content)
        fmt = StatementFormat.PDF

    record = StatementImport(
        user_id=current.id,
        account_id=account.id,
        filename=file.filename or stored_name,
        stored_path=str(stored_path),
        format=fmt,
        rows_parsed=len(rows),
        rows_imported=0,
    )
    db.add(record)
    db.flush()

    # Dedupe within the account using external_id.
    existing_ids = set(
        db.scalars(
            select(Transaction.external_id).where(
                Transaction.user_id == current.id,
                Transaction.account_id == account.id,
                Transaction.external_id.isnot(None),
            )
        )
    )

    imported = 0
    duplicates = 0
    for row in rows:
        if row.external_id and row.external_id in existing_ids:
            duplicates += 1
            continue
        db.add(
            Transaction(
                user_id=current.id,
                account_id=account.id,
                category_id=None,
                statement_import_id=record.id,
                posted_on=row.posted_on,
                description=row.description,
                amount=row.amount,
                external_id=row.external_id,
            )
        )
        if row.external_id:
            existing_ids.add(row.external_id)
        imported += 1

    record.rows_imported = imported
    db.commit()
    db.refresh(record)

    return ImportResponse(
        import_id=record.id,
        rows_parsed=len(rows),
        rows_imported=imported,
        skipped_duplicates=duplicates,
    )
