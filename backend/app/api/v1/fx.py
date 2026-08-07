"""Per-user exchange rates: manual by default, opt-in auto-refresh.

Rates mean: 1 unit of `currency` = `rate` units of the user's display
currency. Changing the display currency deletes saved rates (see auth.py's
update_me) — they were defined against the old target and silently reusing
them would corrupt every converted total.

When the user opts in (`users.fx_auto_refresh`), reads opportunistically
refresh stale auto rates from the external feed (services/fx_feed.py) and
POST /fx/refresh forces one. Manual rates always win — the feed never
overwrites a row whose source is "manual", and a manual PUT over an auto row
flips it to manual.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.debt import Debt
from app.models.fx_rate import RATE_MAX, RATE_QUANTUM, FxRate
from app.models.user import User
from app.services.account_loader import load_display_and_rates
from app.services.fx_feed import refresh_user_rates, refresh_user_rates_detailed

router = APIRouter(prefix="/fx", tags=["fx"])


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
    # "manual" = hand-entered (the feed never touches it); "auto" = fetched by
    # the opt-in feed. The Literal keeps the OpenAPI schema (and the frontend
    # type mirroring it) honest.
    source: Literal["manual", "auto"]

    model_config = {"from_attributes": True}


class FxRefreshOut(BaseModel):
    refreshed_count: int
    # Why nothing was written, when the feed itself was the reason: "cooldown"
    # (a recent failure suppressed the fetch) or "provider_error" (the fetch
    # ran and failed). None on success — including a benign 0 where there was
    # simply nothing to refresh.
    skipped_reason: Literal["cooldown", "provider_error"] | None = None
    rates: list[FxRateOut]


def _list_rates(db, user_id: int) -> list[FxRate]:
    return list(db.scalars(select(FxRate).where(FxRate.user_id == user_id).order_by(FxRate.currency)))


def _refresh_inputs(db, current: User) -> tuple[set[str], str | None]:
    """The currencies the user actually holds (accounts + debts) and the
    display currency to quote them against — what the feed refresh needs."""
    display, _rates = load_display_and_rates(db, current)
    account_currencies = set(db.scalars(select(Account.currency).where(Account.user_id == current.id).distinct()))
    debt_currencies = set(
        db.scalars(select(Debt.currency).where(Debt.user_id == current.id, Debt.currency.is_not(None)).distinct())
    )
    return account_currencies | debt_currencies, display


@router.get("", response_model=list[FxRateOut])
def list_rates(current: CurrentUser, db: DbSession) -> list[FxRate]:
    """List the user's saved rates, opportunistically refreshing stale auto
    rows first — only when the user opted in (`fx_auto_refresh`); a failed
    refresh degrades to last-known rates, never an error."""
    # The opt-in gate lives here so the common opted-out path pays for zero
    # extra queries — then a no-op unless auto rates are stale AND no failure
    # cooldown is running (see services/fx_feed.py).
    if current.fx_auto_refresh:
        currencies_in_use, display = _refresh_inputs(db, current)
        refresh_user_rates(db, current, currencies_in_use, display)
    return _list_rates(db, current.id)


@router.put("", response_model=list[FxRateOut])
def upsert_rates(payload: list[FxRateIn], current: CurrentUser, db: DbSession) -> list[FxRate]:
    """Create or update rates by hand. Every row saved here becomes
    source="manual" — manual always wins: the auto feed never overwrites a
    manual row, and its currency never triggers a fetch again."""
    # Last write wins for duplicate codes in one payload.
    by_currency = {item.currency: item for item in payload}
    if by_currency:
        stmt = pg_insert(FxRate).values(
            [
                {
                    "user_id": current.id,
                    "currency": currency,
                    "rate": item.rate,
                    # A rate without an explicit as-of date is "as of when it
                    # was saved".
                    "as_of": item.as_of or date.today(),
                    "source": "manual",
                }
                for currency, item in sorted(by_currency.items())
            ]
        )
        # ON CONFLICT rather than plain inserts: the opportunistic auto
        # refresh can insert the same (user, currency) concurrently, and a
        # racing plain insert would 500 on the unique constraint. Manual
        # always wins, so an existing row — auto or manual — is overwritten
        # unconditionally and flipped to manual: from here on the feed leaves
        # this currency alone.
        stmt = stmt.on_conflict_do_update(
            constraint="uq_fx_rate_user_currency",
            set_={
                "rate": stmt.excluded.rate,
                "as_of": stmt.excluded.as_of,
                "source": "manual",
                "updated_at": func.now(),
            },
        )
        db.execute(stmt)
        db.commit()
    return _list_rates(db, current.id)


@router.post("/refresh", response_model=FxRefreshOut)
def refresh_rates(current: CurrentUser, db: DbSession) -> FxRefreshOut:
    """Explicit refresh — requires the fx_auto_refresh opt-in (400 without
    it). Bypasses the staleness check but NOT the failure cooldown — a
    refreshed_count of 0 comes with `skipped_reason` saying whether the feed
    skipped ("cooldown") or failed ("provider_error"); last-known rates keep
    serving either way."""
    if not current.fx_auto_refresh:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Automatic FX refresh is not enabled — turn it on via PATCH /auth/me first",
        )
    currencies_in_use, display = _refresh_inputs(db, current)
    result = refresh_user_rates_detailed(db, current, currencies_in_use, display, force=True)
    return FxRefreshOut(
        refreshed_count=result.written,
        skipped_reason=result.skipped_reason,
        rates=_list_rates(db, current.id),
    )


@router.delete("/{currency}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate(currency: str, current: CurrentUser, db: DbSession) -> None:
    rate = db.scalar(select(FxRate).where(FxRate.user_id == current.id, FxRate.currency == currency.upper()))
    if rate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No rate for that currency")
    db.delete(rate)
    db.commit()
