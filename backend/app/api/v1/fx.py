"""User-maintained exchange rates (no API dependency — self-hosted values).

Rates mean: 1 unit of `currency` = `rate` units of the user\'s display
currency. Changing the display currency invalidates saved rates semantically;
the UI says so.
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models.fx_rate import FxRate

router = APIRouter(prefix="/fx", tags=["fx"])


class FxRateIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    rate: float = Field(gt=0)
    as_of: date | None = None


class FxRateOut(BaseModel):
    currency: str
    rate: Decimal
    as_of: date | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[FxRateOut])
def list_rates(current: CurrentUser, db: DbSession) -> list[FxRate]:
    return list(
        db.scalars(select(FxRate).where(FxRate.user_id == current.id).order_by(FxRate.currency))
    )


@router.put("", response_model=list[FxRateOut])
def upsert_rates(payload: list[FxRateIn], current: CurrentUser, db: DbSession) -> list[FxRate]:
    for item in payload:
        currency = item.currency.upper()
        existing = db.scalar(
            select(FxRate).where(FxRate.user_id == current.id, FxRate.currency == currency)
        )
        rate = Decimal(str(item.rate))
        if existing is None:
            db.add(FxRate(user_id=current.id, currency=currency, rate=rate, as_of=item.as_of))
        else:
            existing.rate = rate
            existing.as_of = item.as_of
    db.commit()
    return list(
        db.scalars(select(FxRate).where(FxRate.user_id == current.id).order_by(FxRate.currency))
    )


@router.delete("/{currency}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate(currency: str, current: CurrentUser, db: DbSession) -> None:
    rate = db.scalar(
        select(FxRate).where(FxRate.user_id == current.id, FxRate.currency == currency.upper())
    )
    if rate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No rate for that currency")
    db.delete(rate)
    db.commit()
