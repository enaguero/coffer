from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.schemas.account import AccountCreate, AccountOut, AccountUpdate

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(current: CurrentUser, db: DbSession) -> list[Account]:
    return list(db.scalars(select(Account).where(Account.user_id == current.id).order_by(Account.name)))


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
def update_account(
    account_id: int, payload: AccountUpdate, current: CurrentUser, db: DbSession
) -> Account:
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
