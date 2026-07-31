from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.balance_snapshot import BalanceSnapshot, BalanceSource
from app.models.import_profile import ImportProfile
from app.models.statement import StatementImport
from app.models.transaction import Transaction
from app.schemas.account import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    BalanceSnapshotIn,
    BalanceSnapshotOut,
)
from app.schemas.import_profile import ImportProfileOut, ImportProfileUpsert
from app.schemas.insights import AccountCoverageOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(current: CurrentUser, db: DbSession) -> list[Account]:
    return list(db.scalars(select(Account).where(Account.user_id == current.id).order_by(Account.name)))


@router.get("/coverage", response_model=list[AccountCoverageOut])
def account_coverage(current: CurrentUser, db: DbSession) -> list[AccountCoverageOut]:
    """Data-freshness per account: how far imported data actually reaches.

    Declared before /{account_id} so the literal path wins the route match.
    """
    accounts = list(db.scalars(select(Account).where(Account.user_id == current.id).order_by(Account.name)))
    txn_stats = {
        account_id: (last, count)
        for account_id, last, count in db.execute(
            select(Transaction.account_id, func.max(Transaction.posted_on), func.count())
            .where(Transaction.user_id == current.id)
            .group_by(Transaction.account_id)
        )
    }
    last_imports = {
        account_id: latest
        for account_id, latest in db.execute(
            select(StatementImport.account_id, func.max(StatementImport.created_at))
            .where(StatementImport.user_id == current.id)
            .group_by(StatementImport.account_id)
        )
    }
    last_snapshots = {
        account_id: latest
        for account_id, latest in db.execute(
            select(BalanceSnapshot.account_id, func.max(BalanceSnapshot.as_of))
            .where(BalanceSnapshot.user_id == current.id)
            .group_by(BalanceSnapshot.account_id)
        )
    }
    return [
        AccountCoverageOut(
            account_id=a.id,
            name=a.name,
            type=a.type,
            last_txn_on=txn_stats.get(a.id, (None, 0))[0],
            txn_count=txn_stats.get(a.id, (None, 0))[1],
            last_import_at=last_imports.get(a.id),
            last_snapshot_on=last_snapshots.get(a.id),
        )
        for a in accounts
    ]


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, current: CurrentUser, db: DbSession) -> Account:
    account = Account(user_id=current.id, **payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _get_owned(db, current, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, current: CurrentUser, db: DbSession) -> Account:
    return _get_owned(db, current, account_id)


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, payload: AccountUpdate, current: CurrentUser, db: DbSession) -> Account:
    account = _get_owned(db, current, account_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, key, value)
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int, current: CurrentUser, db: DbSession) -> None:
    account = _get_owned(db, current, account_id)
    db.delete(account)
    db.commit()


# ---- Manual balance snapshots (valuations) ------------------------------------


@router.post(
    "/{account_id}/snapshots",
    response_model=BalanceSnapshotOut,
    status_code=status.HTTP_201_CREATED,
)
def add_snapshot(account_id: int, payload: BalanceSnapshotIn, current: CurrentUser, db: DbSession) -> BalanceSnapshot:
    """Record a manual valuation (pension, property, ISA...) for an account.

    Upserts on (account, date) — statement attestations use the same table and
    a manual entry for the same day overrides them deliberately.
    """
    _get_owned(db, current, account_id)
    snap = db.scalar(
        select(BalanceSnapshot).where(BalanceSnapshot.account_id == account_id, BalanceSnapshot.as_of == payload.as_of)
    )
    if snap is None:
        snap = BalanceSnapshot(
            user_id=current.id,
            account_id=account_id,
            as_of=payload.as_of,
            balance=payload.balance,
            source=BalanceSource.MANUAL,
        )
        db.add(snap)
    else:
        snap.balance = payload.balance
        snap.source = BalanceSource.MANUAL
    db.commit()
    db.refresh(snap)
    return snap


@router.get("/{account_id}/snapshots", response_model=list[BalanceSnapshotOut])
def list_snapshots(account_id: int, current: CurrentUser, db: DbSession) -> list[BalanceSnapshot]:
    _get_owned(db, current, account_id)
    return list(
        db.scalars(
            select(BalanceSnapshot)
            .where(BalanceSnapshot.account_id == account_id)
            .order_by(BalanceSnapshot.as_of.desc())
        )
    )


# ---- Import profile (one per account) -----------------------------------------


def _get_profile(db, account_id: int) -> ImportProfile | None:
    return db.scalar(select(ImportProfile).where(ImportProfile.account_id == account_id))


@router.get("/{account_id}/import-profile", response_model=ImportProfileOut)
def get_import_profile(account_id: int, current: CurrentUser, db: DbSession) -> ImportProfile:
    _get_owned(db, current, account_id)
    profile = _get_profile(db, account_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No import profile")
    return profile


@router.put("/{account_id}/import-profile", response_model=ImportProfileOut)
def upsert_import_profile(
    account_id: int, payload: ImportProfileUpsert, current: CurrentUser, db: DbSession
) -> ImportProfile:
    _get_owned(db, current, account_id)
    profile = _get_profile(db, account_id)
    config = payload.config.model_dump(mode="json")
    if profile is None:
        profile = ImportProfile(
            user_id=current.id,
            account_id=account_id,
            name=payload.name,
            source=payload.source,
            config=config,
        )
        db.add(profile)
    else:
        profile.name = payload.name
        profile.source = payload.source
        profile.config = config
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/{account_id}/import-profile", status_code=status.HTTP_204_NO_CONTENT)
def delete_import_profile(account_id: int, current: CurrentUser, db: DbSession) -> None:
    _get_owned(db, current, account_id)
    profile = _get_profile(db, account_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No import profile")
    db.delete(profile)
    db.commit()
