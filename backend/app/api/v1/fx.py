"""User-maintained exchange rates (no API dependency — self-hosted values).

Rates mean: 1 unit of `currency` = `rate` units of the user's display
currency. Changing the display currency deletes saved rates (see auth.py's
update_me) — they were defined against the old target and silently reusing
them would corrupt every converted total.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models.fx_rate import FxRate

router = APIRouter(prefix="/fx", tags=["fx"])

# Numeric(18, 8): 8 decimal places, 10 integer digits.
RATE_QUANTUM = Decimal("0.00000001")
RATE_MAX = Decimal("9999999999")


class FxRateIn(BaseModel):
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    rate: Decimal = Field(gt=0, le=RATE_MAX)
    as_of: date | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("rate")
    @classmethod
    def _storable(cls, v: Decimal) -> Decimal:
        quantized = v.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        if quantized <= 0:
            raise ValueError(f"rate must be at least {RATE_QUANTUM} (the smallest storable value)")
        return quantized


class FxRateOut(BaseModel):
    currency: str
    rate: Decimal
    as_of: date | None

    model_config = {"from_attributes": True}


def _list_rates(db, user_id: int) -> list[FxRate]:
    return list(db.scalars(select(FxRate).where(FxRate.user_id == user_id).order_by(FxRate.currency)))


@router.get("", response_model=list[FxRateOut])
def list_rates(current: CurrentUser, db: DbSession) -> list[FxRate]:
    return _list_rates(db, current.id)


@router.put("", response_model=list[FxRateOut])
def upsert_rates(payload: list[FxRateIn], current: CurrentUser, db: DbSession) -> list[FxRate]:
    # Last write wins for duplicate codes in one payload — the session doesn't
    # autoflush, so looping adds for the same currency would 500 on commit.
    by_currency = {item.currency: item for item in payload}
    existing = {r.currency: r for r in _list_rates(db, current.id)}
    for currency, item in by_currency.items():
        # A rate without an explicit as-of date is "as of when it was saved".
        as_of = item.as_of or date.today()
        row = existing.get(currency)
        if row is None:
            db.add(FxRate(user_id=current.id, currency=currency, rate=item.rate, as_of=as_of))
        else:
            row.rate = item.rate
            row.as_of = as_of
    db.commit()
    return _list_rates(db, current.id)


@router.delete("/{currency}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate(currency: str, current: CurrentUser, db: DbSession) -> None:
    rate = db.scalar(
        select(FxRate).where(FxRate.user_id == current.id, FxRate.currency == currency.upper())
    )
    if rate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No rate for that currency")
    db.delete(rate)
    db.commit()
