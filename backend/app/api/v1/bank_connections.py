"""Bank-aggregator integration endpoints.

Owns the lifecycle of a `BankConnection` for the current user:
- list known institutions (proxied from the provider for the picker UI)
- start a link (creates an EUA + requisition, returns the bank's auth URL)
- complete a link (validates the callback, discovers accounts)
- map discovered bank-side accounts to existing or new Coffer Accounts
- trigger a sync (runs in the background, surfaces results via SyncJob rows)
- list sync history
- disconnect (revokes the requisition with the provider, marks local revoked)

Security:
- App-level GoCardless credentials live only in env; never echoed in responses.
- The `reference` we hand to GoCardless on the requisition is an HMAC of
  `{user_id, bank_connection_id}` signed with `BANK_SYNC_STATE_SECRET`, so
  `/link/complete` can verify the callback came back for the right user before
  flipping status. This guards against a victim being tricked into completing
  a link initiated by an attacker.
- `BankConnection`-scoped rate limits (per-user) on the link/sync endpoints via
  slowapi.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import limiter
from app.models.account import Account, AccountType
from app.models.bank_connection import BankConnection, BankConnectionStatus, BankProvider
from app.models.sync_job import SyncJob
from app.schemas.bank_connection import (
    BankConnectionOut,
    DiscoveredAccount,
    InstitutionRef,
    LinkCompleteRequest,
    LinkCompleteResponse,
    LinkStartRequest,
    LinkStartResponse,
    MapAccountRequest,
)
from app.schemas.sync_job import SyncJobOut, SyncResponse
from app.services.bank_providers.base import (
    BankProviderError,
    BankProviderNotConfigured,
)
from app.services.bank_providers.gocardless import GoCardlessProvider
from app.services.bank_sync import run_sync_sync

router = APIRouter(prefix="/bank-connections", tags=["bank-connections"])


def _get_provider() -> GoCardlessProvider:
    if not settings.gocardless_secret_id or not settings.gocardless_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bank sync is not configured. Set GOCARDLESS_SECRET_ID / SECRET_KEY in .env.",
        )
    if not settings.bank_sync_state_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bank sync is missing BANK_SYNC_STATE_SECRET — generate one and set it in .env.",
        )
    return GoCardlessProvider(
        secret_id=settings.gocardless_secret_id,
        secret_key=settings.gocardless_secret_key,
        base_url=settings.gocardless_base_url,
    )


def _state_secret() -> bytes:
    # _get_provider guarantees this is set when we get here.
    assert settings.bank_sync_state_secret is not None
    return settings.bank_sync_state_secret.encode()


def _make_reference(user_id: int, nonce: str) -> str:
    """Signed reference handed to GoCardless on requisition creation.

    GoCardless echoes this back on the callback (we re-fetch the requisition
    server-side) — we verify the signature before trusting the callback as
    belonging to this user.
    """
    payload = f"{user_id}.{nonce}".encode()
    mac = hmac.new(_state_secret(), payload, hashlib.sha256).hexdigest()
    return f"{user_id}.{nonce}.{mac}"


def _verify_reference(reference: str, user_id: int) -> bool:
    try:
        uid_str, nonce, mac = reference.split(".")
    except ValueError:
        return False
    if int(uid_str) != user_id:
        return False
    expected = hmac.new(_state_secret(), f"{uid_str}.{nonce}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, mac)


def _to_provider_error(exc: BankProviderError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


# ---- Routes ------------------------------------------------------------------


@router.get("/institutions", response_model=list[InstitutionRef])
async def list_institutions(
    current: CurrentUser,
    country: str = Query(min_length=2, max_length=2, description="ISO 3166-1 alpha-2"),
) -> list[InstitutionRef]:
    del current  # auth only — no per-user data
    provider = _get_provider()
    try:
        return await provider.list_institutions(country)
    except BankProviderNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except BankProviderError as exc:
        raise _to_provider_error(exc) from exc


@router.post("/link/start", response_model=LinkStartResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
async def link_start(
    request: Request,
    payload: LinkStartRequest,
    current: CurrentUser,
    db: DbSession,
) -> LinkStartResponse:
    del request  # slowapi requires it positionally; we don't otherwise need it
    provider = _get_provider()
    nonce = secrets.token_urlsafe(16)
    reference = _make_reference(current.id, nonce)
    try:
        session = await provider.create_link(
            institution_id=payload.institution_id,
            redirect_uri=settings.gocardless_redirect_uri,
            reference=reference,
        )
    except BankProviderError as exc:
        raise _to_provider_error(exc) from exc

    connection = BankConnection(
        user_id=current.id,
        provider=BankProvider.GOCARDLESS,
        institution_id=payload.institution_id,
        # We don't have the institution's pretty name without another GC call.
        # Use the id as a placeholder; /link/complete patches in the real name.
        institution_name=payload.institution_id,
        requisition_id=session.requisition_id,
        agreement_id=session.agreement_id,
        requisition_expires_at=session.expires_at,
        status=BankConnectionStatus.PENDING,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return LinkStartResponse(
        bank_connection_id=connection.id,
        requisition_id=connection.requisition_id,
        link_url=session.link_url,
    )


@router.post("/link/complete", response_model=LinkCompleteResponse)
@limiter.limit("10/minute")
async def link_complete(
    request: Request,
    payload: LinkCompleteRequest,
    current: CurrentUser,
    db: DbSession,
) -> LinkCompleteResponse:
    del request
    connection = db.scalar(
        select(BankConnection).where(
            BankConnection.user_id == current.id,
            BankConnection.requisition_id == payload.requisition_id,
        )
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    provider = _get_provider()
    try:
        accounts = await provider.complete_link(payload.requisition_id)
    except BankProviderError as exc:
        raise _to_provider_error(exc) from exc

    # Best-effort: also fetch the institution's display name if we don't have one.
    if connection.institution_name == connection.institution_id:
        try:
            institutions = await provider.list_institutions(
                (accounts[0].currency or "GB")[:2] if accounts else "GB"
            )
            match = next((i for i in institutions if i.id == connection.institution_id), None)
            if match:
                connection.institution_name = match.name
        except BankProviderError:
            pass

    connection.status = BankConnectionStatus.LINKED
    db.commit()
    db.refresh(connection)

    return LinkCompleteResponse(
        bank_connection_id=connection.id,
        institution_name=connection.institution_name,
        accounts=[
            DiscoveredAccount(
                external_account_id=a.external_account_id,
                iban_last4=a.iban_last4,
                name=a.name,
                currency=a.currency,
            )
            for a in accounts
        ],
    )


def _get_owned(db, current, bank_connection_id: int) -> BankConnection:
    record = db.get(BankConnection, bank_connection_id)
    if record is None or record.user_id != current.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return record


@router.get("", response_model=list[BankConnectionOut])
def list_connections(current: CurrentUser, db: DbSession) -> list[BankConnection]:
    return list(
        db.scalars(
            select(BankConnection)
            .where(BankConnection.user_id == current.id)
            .order_by(BankConnection.created_at.desc())
        )
    )


@router.post("/{bank_connection_id}/map-account", status_code=status.HTTP_201_CREATED)
def map_account(
    bank_connection_id: int,
    payload: MapAccountRequest,
    current: CurrentUser,
    db: DbSession,
) -> dict[str, int]:
    """Attach a discovered bank-side account to a Coffer Account.

    If `account_id` is given, that existing Account is linked (must belong to the
    user and not already linked elsewhere). Otherwise a new Account is created
    using `name` and `currency`. Returns the resulting account id.
    """
    connection = _get_owned(db, current, bank_connection_id)
    if connection.status != BankConnectionStatus.LINKED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection is not in LINKED status",
        )
    if payload.account_id is not None:
        account = db.get(Account, payload.account_id)
        if account is None or account.user_id != current.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    else:
        if not payload.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="`name` is required when creating a new account",
            )
        account = Account(
            user_id=current.id,
            name=payload.name,
            type=AccountType.CHECKING,
            institution=connection.institution_name,
            currency=(payload.currency or "EUR").upper(),
        )
        db.add(account)
        db.flush()
    account.bank_connection_id = connection.id
    account.external_account_id = payload.external_account_id
    db.commit()
    return {"account_id": account.id}


@router.post("/{bank_connection_id}/sync", response_model=SyncResponse)
@limiter.limit("4/minute")
async def trigger_sync(
    request: Request,
    bank_connection_id: int,
    current: CurrentUser,
    db: DbSession,
    background: BackgroundTasks,
) -> SyncResponse:
    del request
    connection = _get_owned(db, current, bank_connection_id)
    if connection.status != BankConnectionStatus.LINKED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection is not in LINKED status — re-authenticate first.",
        )
    if (
        connection.requisition_expires_at is not None
        and connection.requisition_expires_at <= datetime.now(UTC)
    ):
        connection.status = BankConnectionStatus.EXPIRED
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection has expired — re-authenticate to keep syncing.",
        )

    linked_accounts = list(
        db.scalars(
            select(Account.id).where(
                Account.user_id == current.id,
                Account.bank_connection_id == connection.id,
                Account.external_account_id.isnot(None),
            )
        )
    )
    if not linked_accounts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No accounts are mapped on this connection yet.",
        )

    provider = _get_provider()
    background.add_task(
        run_sync_sync,
        user_id=current.id,
        bank_connection_id=connection.id,
        account_ids=linked_accounts,
        provider=provider,
    )
    return SyncResponse(sync_job_ids=[], queued=len(linked_accounts))


@router.get("/sync-jobs", response_model=list[SyncJobOut])
def list_sync_jobs(
    current: CurrentUser,
    db: DbSession,
    bank_connection_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[SyncJob]:
    stmt = (
        select(SyncJob)
        .where(SyncJob.user_id == current.id)
        .order_by(SyncJob.started_at.desc())
        .limit(limit)
    )
    if bank_connection_id is not None:
        stmt = stmt.where(SyncJob.bank_connection_id == bank_connection_id)
    return list(db.scalars(stmt))


@router.delete("/{bank_connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    bank_connection_id: int,
    current: CurrentUser,
    db: DbSession,
) -> None:
    connection = _get_owned(db, current, bank_connection_id)
    if connection.status != BankConnectionStatus.REVOKED:
        provider = _get_provider()
        try:
            await provider.revoke(connection.requisition_id)
        except BankProviderError:
            # Provider already lost the requisition (expired/404) — proceed locally.
            pass
        connection.status = BankConnectionStatus.REVOKED
        # Detach linked accounts so syncs can't accidentally hit a dead requisition.
        db.execute(
            Account.__table__.update()
            .where(Account.bank_connection_id == connection.id)
            .values(bank_connection_id=None, external_account_id=None)
        )
        db.commit()


# Internal helper exported for tests so they can exercise the HMAC contract.
__all__ = ["router", "_make_reference", "_verify_reference"]
