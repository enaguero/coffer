"""Statement imports.

Two flows live here:

- `/imports/upload` — legacy one-shot: parse + commit in one request.
- `/imports/preview` → `/imports/{id}/confirm` — review-before-commit: user
  sees parsed rows with auto-categorization suggestions and dedup flags, then
  selects which to import (and optionally adjusts categories) before commit.
"""

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.balance_snapshot import BalanceSnapshot, BalanceSource
from app.models.category import Category
from app.models.category_rule import CategoryRule
from app.models.import_profile import ImportProfile
from app.models.statement import StatementImport, StatementImportStatus
from app.models.transaction import Transaction
from app.schemas.statement import (
    ConfirmRequest,
    ImportResponse,
    PreviewResponse,
    PreviewRow,
    StatementImportOut,
)
from app.services.categorization import compile_rules, match_category
from app.services.import_engine import resolve_and_parse
from app.services.import_engine.profile import ImportProfileConfig

router = APIRouter(prefix="/imports", tags=["imports"])

ALLOWED_EXTENSIONS = {".csv", ".pdf", ".ofx", ".qfx", ".qif"}


def _validate_upload(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {suffix}. Use .csv, .ofx, .qif, or .pdf",
        )
    return suffix


def _load_profile_config(db, account_id: int) -> ImportProfileConfig | None:
    profile = db.scalar(select(ImportProfile).where(ImportProfile.account_id == account_id))
    if profile is None:
        return None
    try:
        return ImportProfileConfig.model_validate(profile.config)
    except ValueError:
        # A profile saved by an older build may no longer validate; ignore it
        # rather than block imports.
        return None




def _persist_file(user_id: int, content: bytes, suffix: str) -> tuple[Path, str]:
    upload_dir = Path(settings.upload_dir) / str(user_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(content)
    return stored_path, stored_name


def _existing_external_ids(db, user_id: int, account_id: int) -> set[str]:
    return set(
        db.scalars(
            select(Transaction.external_id).where(
                Transaction.user_id == user_id,
                Transaction.account_id == account_id,
                Transaction.external_id.isnot(None),
            )
        )
    )


def _get_user_account(db, current, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


def _record_statement_balance(db, user_id: int, account_id: int, as_of, balance) -> None:
    """Upsert the statement-attested balance for (account, day)."""
    if as_of is None or balance is None:
        return
    snap = db.scalar(
        select(BalanceSnapshot).where(
            BalanceSnapshot.account_id == account_id, BalanceSnapshot.as_of == as_of
        )
    )
    if snap is None:
        db.add(
            BalanceSnapshot(
                user_id=user_id,
                account_id=account_id,
                as_of=as_of,
                balance=balance,
                source=BalanceSource.STATEMENT,
            )
        )
    else:
        snap.balance = balance
        snap.source = BalanceSource.STATEMENT


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
    account = _get_user_account(db, current, account_id)
    suffix = _validate_upload(file)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    outcome = resolve_and_parse(content, suffix, account, _load_profile_config(db, account.id))
    rows = outcome.rows
    stored_path, stored_name = _persist_file(current.id, content, suffix)

    record = StatementImport(
        user_id=current.id,
        account_id=account.id,
        filename=file.filename or stored_name,
        stored_path=str(stored_path),
        format=outcome.format,
        status=StatementImportStatus.COMMITTED,
        rows_parsed=len(rows),
        rows_imported=0,
    )
    db.add(record)
    db.flush()

    existing_ids = _existing_external_ids(db, current.id, account.id)
    rules = compile_rules(
        db.scalars(
            select(CategoryRule)
            .where(CategoryRule.user_id == current.id)
            .order_by(CategoryRule.priority, CategoryRule.id)
        )
    )

    imported = 0
    duplicates = 0
    auto_categorized = 0
    for row in rows:
        if row.external_id and row.external_id in existing_ids:
            duplicates += 1
            continue
        category_id = match_category(row.description, rules)
        if category_id is not None:
            auto_categorized += 1
        db.add(
            Transaction(
                user_id=current.id,
                account_id=account.id,
                category_id=category_id,
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
    _record_statement_balance(
        db, current.id, account.id, outcome.closing_balance_date, outcome.closing_balance
    )
    db.commit()
    db.refresh(record)
    return ImportResponse(
        import_id=record.id,
        rows_parsed=len(rows),
        rows_imported=imported,
        skipped_duplicates=duplicates,
        auto_categorized=auto_categorized,
    )


@router.post("/preview", response_model=PreviewResponse, status_code=status.HTTP_201_CREATED)
async def preview_statement(
    current: CurrentUser,
    db: DbSession,
    account_id: int = Form(...),
    file: UploadFile = File(...),
) -> PreviewResponse:
    account = _get_user_account(db, current, account_id)
    suffix = _validate_upload(file)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    profile_config = _load_profile_config(db, account.id)
    outcome = resolve_and_parse(content, suffix, account, profile_config)
    rows = outcome.rows
    stored_path, stored_name = _persist_file(current.id, content, suffix)

    existing_ids = _existing_external_ids(db, current.id, account.id)
    rules = compile_rules(
        db.scalars(
            select(CategoryRule)
            .where(CategoryRule.user_id == current.id)
            .order_by(CategoryRule.priority, CategoryRule.id)
        )
    )

    preview_rows: list[dict[str, Any]] = []
    dup_count = 0
    auto_count = 0
    for idx, row in enumerate(rows):
        is_dup = bool(row.external_id and row.external_id in existing_ids)
        if is_dup:
            dup_count += 1
        suggested = match_category(row.description, rules)
        if suggested is not None:
            auto_count += 1
        preview_rows.append(
            {
                "id": idx,
                "external_id": row.external_id,
                "posted_on": row.posted_on.isoformat(),
                "description": row.description,
                # Decimal isn't JSON-native — serialize to string and reparse on confirm.
                "amount": str(row.amount),
                "suggested_category_id": suggested,
                "is_duplicate": is_dup,
            }
        )

    record = StatementImport(
        user_id=current.id,
        account_id=account.id,
        filename=file.filename or stored_name,
        stored_path=str(stored_path),
        format=outcome.format,
        status=StatementImportStatus.PREVIEW,
        rows_parsed=len(rows),
        rows_imported=0,
        preview_rows=preview_rows,
        closing_balance=outcome.closing_balance,
        closing_balance_date=outcome.closing_balance_date,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return PreviewResponse(
        import_id=record.id,
        account_id=account.id,
        filename=record.filename,
        rows=[
            PreviewRow(
                id=r["id"],
                external_id=r["external_id"],
                posted_on=r["posted_on"],
                description=r["description"],
                amount=Decimal(r["amount"]),
                suggested_category_id=r["suggested_category_id"],
                is_duplicate=r["is_duplicate"],
            )
            for r in preview_rows
        ],
        duplicate_count=dup_count,
        auto_categorized_count=auto_count,
        source=outcome.source,
        warnings=outcome.warnings,
        inferred_config=(
            outcome.inferred_config.model_dump(mode="json")
            if outcome.inferred_config is not None
            else None
        ),
        has_profile=profile_config is not None,
    )


def _get_owned_import(db, current, import_id: int) -> StatementImport:
    record = db.get(StatementImport, import_id)
    if record is None or record.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    return record


@router.post("/{import_id}/confirm", response_model=ImportResponse)
def confirm_preview(
    import_id: int, payload: ConfirmRequest, current: CurrentUser, db: DbSession
) -> ImportResponse:
    record = _get_owned_import(db, current, import_id)
    if record.status != StatementImportStatus.PREVIEW or record.preview_rows is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Import is not a pending preview"
        )

    overrides = {r.id: r for r in payload.rows}
    existing_ids = _existing_external_ids(db, current.id, record.account_id)

    # Pre-validate that any user-provided category overrides belong to the caller.
    overridden_cat_ids = {
        r.category_id for r in payload.rows if r.category_id is not None and not r.skip
    }
    if overridden_cat_ids:
        owned = set(
            db.scalars(
                select(Category.id).where(
                    Category.user_id == current.id, Category.id.in_(overridden_cat_ids)
                )
            )
        )
        bad = overridden_cat_ids - owned
        if bad:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category not found: {sorted(bad)[0]}",
            )

    imported = 0
    duplicates = 0
    auto_categorized = 0
    for row in record.preview_rows:
        override = overrides.get(row["id"])
        if override is not None and override.skip:
            continue
        if row["is_duplicate"]:
            duplicates += 1
            continue
        # The client's category choice wins; falling back to the auto-suggestion if absent.
        if override is not None and override.category_id is not None:
            category_id: int | None = override.category_id
        else:
            category_id = row["suggested_category_id"]
        if category_id is not None:
            auto_categorized += 1
        ext = row["external_id"]
        if ext and ext in existing_ids:
            duplicates += 1
            continue
        db.add(
            Transaction(
                user_id=current.id,
                account_id=record.account_id,
                category_id=category_id,
                statement_import_id=record.id,
                posted_on=row["posted_on"],
                description=row["description"],
                amount=Decimal(row["amount"]),
                external_id=ext,
            )
        )
        if ext:
            existing_ids.add(ext)
        imported += 1

    record.status = StatementImportStatus.COMMITTED
    record.rows_imported = imported
    record.preview_rows = None  # free the JSON once committed
    _record_statement_balance(
        db, current.id, record.account_id, record.closing_balance_date, record.closing_balance
    )
    db.commit()
    db.refresh(record)
    return ImportResponse(
        import_id=record.id,
        rows_parsed=record.rows_parsed,
        rows_imported=imported,
        skipped_duplicates=duplicates,
        auto_categorized=auto_categorized,
    )


@router.delete("/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_preview(import_id: int, current: CurrentUser, db: DbSession) -> None:
    record = _get_owned_import(db, current, import_id)
    if record.status != StatementImportStatus.PREVIEW:
        # Committed imports keep their record + transactions; we don't cascade-delete those.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Only previews can be discarded"
        )
    record.status = StatementImportStatus.DISCARDED
    record.preview_rows = None
    db.commit()
