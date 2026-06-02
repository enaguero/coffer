from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.cashflow import CashflowEntry, CashflowKind, CashflowLine
from app.models.category import Category
from app.schemas.cashflow import (
    CashflowCurrencyTotals,
    CashflowEntryBulk,
    CashflowEntryIn,
    CashflowEntryOut,
    CashflowEntryUpsert,
    CashflowGridOut,
    CashflowLineCreate,
    CashflowLineOut,
    CashflowLineUpdate,
    CashflowMonth,
    CashflowMonthTotal,
)

router = APIRouter(prefix="/cashflow", tags=["cashflow"])


# ----- helpers -----


def _validate_line_links(
    db, current, account_id: int | None, category_id: int | None
) -> None:
    if account_id is not None:
        acc = db.get(Account, account_id)
        if acc is None or acc.user_id != current.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Account not found"
            )
    if category_id is not None:
        cat = db.get(Category, category_id)
        if cat is None or cat.user_id != current.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Category not found"
            )


def _get_owned_line(db, current, line_id: int) -> CashflowLine:
    line = db.get(CashflowLine, line_id)
    if line is None or line.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cashflow line not found")
    return line


def _serialize_line(line: CashflowLine) -> CashflowLineOut:
    return CashflowLineOut(
        id=line.id,
        name=line.name,
        kind=line.kind,
        country=line.country,
        currency=line.currency,
        account_id=line.account_id,
        category_id=line.category_id,
        sort_order=line.sort_order,
        is_active=line.is_active,
        notes=line.notes,
        entries=[
            CashflowEntryIn(year=e.year, month=e.month, amount=Decimal(e.amount))
            for e in sorted(line.entries, key=lambda e: (e.year, e.month))
        ],
    )


def _month_range(start_year: int, start_month: int, months: int) -> list[CashflowMonth]:
    out: list[CashflowMonth] = []
    y, m = start_year, start_month
    for _ in range(months):
        out.append(CashflowMonth(year=y, month=m))
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


# ----- line CRUD -----


@router.get("/lines", response_model=list[CashflowLineOut])
def list_lines(
    current: CurrentUser,
    db: DbSession,
    country: str | None = None,
    kind: CashflowKind | None = None,
    is_active: bool | None = None,
) -> list[CashflowLineOut]:
    stmt = select(CashflowLine).where(CashflowLine.user_id == current.id)
    if country is not None:
        stmt = stmt.where(CashflowLine.country == country.upper())
    if kind is not None:
        stmt = stmt.where(CashflowLine.kind == kind)
    if is_active is not None:
        stmt = stmt.where(CashflowLine.is_active == is_active)
    stmt = stmt.order_by(CashflowLine.sort_order, CashflowLine.id)
    return [_serialize_line(line) for line in db.scalars(stmt).unique()]


@router.post("/lines", response_model=CashflowLineOut, status_code=status.HTTP_201_CREATED)
def create_line(
    payload: CashflowLineCreate, current: CurrentUser, db: DbSession
) -> CashflowLineOut:
    _validate_line_links(db, current, payload.account_id, payload.category_id)
    line = CashflowLine(user_id=current.id, **payload.model_dump())
    db.add(line)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A cashflow line with this name already exists",
        ) from e
    db.refresh(line)
    return _serialize_line(line)


@router.patch("/lines/{line_id}", response_model=CashflowLineOut)
def update_line(
    line_id: int, payload: CashflowLineUpdate, current: CurrentUser, db: DbSession
) -> CashflowLineOut:
    line = _get_owned_line(db, current, line_id)
    updates = payload.model_dump(exclude_unset=True)
    if "account_id" in updates or "category_id" in updates:
        _validate_line_links(
            db, current, updates.get("account_id", line.account_id),
            updates.get("category_id", line.category_id),
        )
    for key, value in updates.items():
        setattr(line, key, value)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A cashflow line with this name already exists",
        ) from e
    db.refresh(line)
    return _serialize_line(line)


@router.delete("/lines/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_line(line_id: int, current: CurrentUser, db: DbSession) -> None:
    line = _get_owned_line(db, current, line_id)
    db.delete(line)
    db.commit()


# ----- entry upsert / bulk / delete -----


def _upsert_entry(
    db, current, line: CashflowLine, year: int, month: int, amount: Decimal
) -> CashflowEntry:
    entry = db.scalars(
        select(CashflowEntry).where(
            CashflowEntry.line_id == line.id,
            CashflowEntry.year == year,
            CashflowEntry.month == month,
        )
    ).first()
    if entry is None:
        entry = CashflowEntry(
            user_id=current.id, line_id=line.id, year=year, month=month, amount=amount
        )
        db.add(entry)
    else:
        entry.amount = amount
    return entry


@router.put("/entries", response_model=CashflowEntryOut)
def upsert_entry(
    payload: CashflowEntryUpsert, current: CurrentUser, db: DbSession
) -> CashflowEntry:
    line = _get_owned_line(db, current, payload.line_id)
    entry = _upsert_entry(db, current, line, payload.year, payload.month, payload.amount)
    db.commit()
    db.refresh(entry)
    return entry


@router.post(
    "/entries/bulk", response_model=list[CashflowEntryOut], status_code=status.HTTP_200_OK
)
def bulk_upsert_entries(
    payload: CashflowEntryBulk, current: CurrentUser, db: DbSession
) -> list[CashflowEntry]:
    # Pre-validate all line ownerships in one query so we either accept the whole batch
    # or reject it; matches the simulator "Save N changes" contract.
    line_ids = {e.line_id for e in payload.entries}
    if line_ids:
        owned = set(
            db.scalars(
                select(CashflowLine.id).where(
                    CashflowLine.user_id == current.id, CashflowLine.id.in_(line_ids)
                )
            )
        )
        missing = line_ids - owned
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Cashflow line(s) not found: {sorted(missing)}",
            )
    results: list[CashflowEntry] = []
    for item in payload.entries:
        line = db.get(CashflowLine, item.line_id)
        results.append(
            _upsert_entry(db, current, line, item.year, item.month, item.amount)
        )
    db.commit()
    for e in results:
        db.refresh(e)
    return results


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(entry_id: int, current: CurrentUser, db: DbSession) -> None:
    entry = db.get(CashflowEntry, entry_id)
    if entry is None or entry.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cashflow entry not found")
    db.delete(entry)
    db.commit()


# ----- grid view -----


@router.get("/grid", response_model=CashflowGridOut)
def grid(
    current: CurrentUser,
    db: DbSession,
    start_year: int = Query(..., ge=1900, le=3000),
    start_month: int = Query(..., ge=1, le=12),
    months: int = Query(12, ge=1, le=36),
    country: str | None = None,
    currency: str | None = None,
    include_inactive: bool = False,
) -> CashflowGridOut:
    month_list = _month_range(start_year, start_month, months)
    month_set = {(m.year, m.month) for m in month_list}

    stmt = select(CashflowLine).where(CashflowLine.user_id == current.id)
    if country is not None:
        stmt = stmt.where(CashflowLine.country == country.upper())
    if currency is not None:
        stmt = stmt.where(CashflowLine.currency == currency.upper())
    if not include_inactive:
        stmt = stmt.where(CashflowLine.is_active.is_(True))
    stmt = stmt.order_by(CashflowLine.sort_order, CashflowLine.id)
    lines = list(db.scalars(stmt).unique())

    serialized_lines: list[CashflowLineOut] = []
    # Currency → (year, month) → {income, expense}
    totals: dict[str, dict[tuple[int, int], dict[str, Decimal]]] = defaultdict(
        lambda: defaultdict(lambda: {"income": Decimal("0"), "expense": Decimal("0")})
    )
    for line in lines:
        # Trim each line's entries to the visible window so the response stays small.
        visible_entries = [e for e in line.entries if (e.year, e.month) in month_set]
        out = CashflowLineOut(
            id=line.id,
            name=line.name,
            kind=line.kind,
            country=line.country,
            currency=line.currency,
            account_id=line.account_id,
            category_id=line.category_id,
            sort_order=line.sort_order,
            is_active=line.is_active,
            notes=line.notes,
            entries=[
                CashflowEntryIn(year=e.year, month=e.month, amount=Decimal(e.amount))
                for e in sorted(visible_entries, key=lambda e: (e.year, e.month))
            ],
        )
        serialized_lines.append(out)
        bucket = "income" if line.kind == CashflowKind.INCOME else "expense"
        for entry in visible_entries:
            totals[line.currency][(entry.year, entry.month)][bucket] += Decimal(entry.amount)

    totals_by_currency: list[CashflowCurrencyTotals] = []
    for cur in sorted(totals.keys()):
        month_totals: list[CashflowMonthTotal] = []
        for m in month_list:
            cell = totals[cur].get((m.year, m.month), {"income": Decimal("0"), "expense": Decimal("0")})
            inc = cell["income"]
            exp = cell["expense"]
            month_totals.append(
                CashflowMonthTotal(
                    year=m.year,
                    month=m.month,
                    income=inc,
                    expense=exp,
                    net=inc - exp,
                )
            )
        totals_by_currency.append(CashflowCurrencyTotals(currency=cur, months=month_totals))

    return CashflowGridOut(
        months=month_list, lines=serialized_lines, totals_by_currency=totals_by_currency
    )
