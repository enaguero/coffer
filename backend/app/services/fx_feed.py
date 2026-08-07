"""Opt-in FX rate feed: fetch provider rates into the user's FxRate table.

Provider seam: `fetch_rates` is the only function that talks to the network.
The default provider is the ExchangeRate-API open endpoint
(`{FX_FEED_URL}/{base}` — no key, ~166 currencies including CLP); a
self-hosted Frankfurter serving the same shape works behind the same seam.

Semantics: the provider returns units-per-base (1 base = X foreign), but a
stored FxRate means the INVERSE — 1 unit of foreign currency = `rate` units
of the display currency — so every fetched value is stored as 1/X, quantized
to the same Numeric(18, 8) grid manual entry uses. A value failing the
manual-entry bounds (positive, <= RATE_MAX, valid 3-letter code) is a
per-currency failure: never persisted, never raised.

Manual rates always win: refresh upserts only rows whose source is "auto",
and currencies covered by a manual row never trigger a fetch at all.

Failure cooldown: a failed fetch starts a 15-minute per-user cooldown so a
provider outage never becomes a per-request latency tax. The cooldown lives
in a module-level dict — in-process on purpose; it resets on restart, which
is acceptable for this single-process app.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.fx_rate import RATE_MAX, RATE_QUANTUM, FxRate
from app.models.user import User

FETCH_TIMEOUT_SECONDS = 3.0
FAILURE_COOLDOWN_SECONDS = 15 * 60
# `as_of` is date-granular, so ">1 day old" means "not refreshed today or
# yesterday-at-latest": a row dated yesterday is due, a row dated today is not.
AUTO_MAX_AGE = timedelta(days=1)

# user_id -> time.monotonic() of the last failed fetch (see module docstring).
_failure_cooldowns: dict[int, float] = {}


class FxFeedError(Exception):
    """Any fetch/network/shape failure — callers degrade to last-known rates."""


def _http_get(url: str) -> httpx.Response:
    """The network boundary — tests monkeypatch this; nothing else calls out."""
    return httpx.get(url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)


def _inverted(value: object) -> Decimal | None:
    """Provider units-per-base -> Coffer rate (1 foreign = X display), or None
    when the value is malformed or fails the manual-entry storage bounds."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        units_per_base = Decimal(str(value))
    except InvalidOperation:
        return None
    if not units_per_base.is_finite() or units_per_base <= 0:
        return None
    try:
        rate = (Decimal(1) / units_per_base).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if rate <= 0 or rate > RATE_MAX:
        return None
    return rate


def fetch_rates(base_currency: str, currencies: set[str]) -> dict[str, Decimal]:
    """One provider call for `base_currency`; returns Coffer-semantics rates
    for the requested currencies whose payload values pass validation (a bad
    or missing entry is silently dropped). Raises FxFeedError when the fetch
    or the payload shape as a whole fails."""
    url = f"{settings.fx_feed_url.rstrip('/')}/{base_currency}"
    try:
        response = _http_get(url)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise FxFeedError(f"FX feed request failed: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("result") != "success":
        raise FxFeedError("FX feed returned an unsuccessful payload")
    raw = payload.get("rates")
    if not isinstance(raw, dict):
        raise FxFeedError("FX feed payload has no rates table")
    fetched: dict[str, Decimal] = {}
    for code in currencies:
        if len(code) != 3 or not code.isalpha():
            continue
        rate = _inverted(raw.get(code.upper()))
        if rate is not None:
            fetched[code.upper()] = rate
    return fetched


def _is_stale(needed: set[str], auto_rows: list[FxRate]) -> bool:
    covered = {r.currency for r in auto_rows}
    if needed - covered:
        return True
    newest = max((r.as_of for r in auto_rows if r.as_of is not None), default=None)
    return newest is None or date.today() - newest >= AUTO_MAX_AGE


def refresh_user_rates(
    db: Session,
    user: User,
    currencies_in_use: set[str],
    display_currency: str | None,
    *,
    force: bool = False,
) -> int:
    """Upsert auto rows for the in-use currencies not covered by manual rates.

    Returns the number of rows written. No-ops (returning 0) unless the user
    opted in; also when there is no display currency to quote against, every
    needed currency is manual, auto rates are fresh (unless `force`), or the
    failure cooldown is active (`force` does NOT bypass the cooldown). A fetch
    failure leaves existing rows untouched and starts the cooldown.
    """
    if not user.fx_auto_refresh or not display_currency:
        return 0
    needed = {c for c in currencies_in_use if c != display_currency}
    if not needed:
        return 0
    rows = list(db.scalars(select(FxRate).where(FxRate.user_id == user.id, FxRate.currency.in_(needed))))
    # Manual rates always win — their currencies never trigger a fetch.
    needed -= {r.currency for r in rows if r.source == "manual"}
    if not needed:
        return 0
    if not force and not _is_stale(needed, [r for r in rows if r.source == "auto"]):
        return 0
    last_failure = _failure_cooldowns.get(user.id)
    if last_failure is not None and time.monotonic() - last_failure < FAILURE_COOLDOWN_SECONDS:
        return 0
    try:
        fetched = fetch_rates(display_currency, needed)
    except FxFeedError:
        _failure_cooldowns[user.id] = time.monotonic()
        return 0
    _failure_cooldowns.pop(user.id, None)
    if not fetched:
        return 0
    stmt = pg_insert(FxRate).values(
        [
            {"user_id": user.id, "currency": code, "rate": rate, "as_of": date.today(), "source": "auto"}
            for code, rate in sorted(fetched.items())
        ]
    )
    # ON CONFLICT so concurrent first-loads can't trip the unique constraint;
    # the WHERE guard makes "never overwrite manual" atomic even if a manual
    # PUT landed between our read and this write.
    stmt = stmt.on_conflict_do_update(
        constraint="uq_fx_rate_user_currency",
        set_={
            "rate": stmt.excluded.rate,
            "as_of": stmt.excluded.as_of,
            "source": "auto",
            "updated_at": func.now(),
        },
        where=FxRate.__table__.c.source != "manual",
    )
    # RETURNING (rather than rowcount, which psycopg can report as -1 here)
    # counts exactly the rows written — conflicts skipped by the manual guard
    # return nothing.
    written = len(db.execute(stmt.returning(FxRate.id)).all())
    db.commit()
    return written
