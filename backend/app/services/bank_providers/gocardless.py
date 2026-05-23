"""GoCardless Bank Account Data provider (formerly Nordigen).

Free for personal use under PSD2 Open Banking. 2,500+ EU/UK banks.
API portal and docs are reachable from https://bankaccountdata.gocardless.com/.

Auth model:
- App-level SECRET_ID + SECRET_KEY (from .env) mint a 24h JWT access token via
  POST /token/new/. Cached in-memory per process — re-minting is cheap.
- Per-user identifiers (`requisition_id`, `account_id`, `agreement_id`) are
  opaque references — they cannot be used to access bank data without the
  app-level credentials. So we do not encrypt them at rest.

Rate limit: 4 calls per endpoint per account per day. Sync at most a few times
a day. The sync orchestrator's 3-day lookback covers any in-between misses.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.schemas.bank_connection import InstitutionRef
from app.services.bank_providers.base import (
    BankProvider,
    BankProviderError,
    BankProviderNotConfigured,
    LinkedAccountRef,
    LinkSession,
    NormalizedTxn,
)

log = logging.getLogger(__name__)

# Re-mint tokens this many seconds before they'd actually expire, so we don't
# race a request right at the boundary.
_TOKEN_REFRESH_SLACK_SECONDS = 60
# Default PSD2 access window. Banks can refuse longer values; 90 days is the
# safest universal default.
DEFAULT_ACCESS_VALID_FOR_DAYS = 90
DEFAULT_MAX_HISTORICAL_DAYS = 90


class GoCardlessProvider(BankProvider):
    def __init__(
        self,
        *,
        secret_id: str | None,
        secret_key: str | None,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not secret_id or not secret_key:
            raise BankProviderNotConfigured(
                "GOCARDLESS_SECRET_ID and GOCARDLESS_SECRET_KEY must be set in .env"
            )
        self._secret_id = secret_id
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0  # epoch seconds

    # ---- Public BankProvider API ------------------------------------------------

    async def list_institutions(self, country: str) -> list[InstitutionRef]:
        data = await self._request("GET", f"/institutions/?country={country.upper()}")
        return [
            InstitutionRef(
                id=item["id"],
                name=item.get("name", item["id"]),
                bic=item.get("bic"),
                countries=item.get("countries", []),
                logo_url=item.get("logo"),
            )
            for item in data
        ]

    async def create_link(
        self,
        *,
        institution_id: str,
        redirect_uri: str,
        reference: str,
        user_language: str | None = None,
    ) -> LinkSession:
        agreement = await self._request(
            "POST",
            "/agreements/enduser/",
            json={
                "institution_id": institution_id,
                "max_historical_days": DEFAULT_MAX_HISTORICAL_DAYS,
                "access_valid_for_days": DEFAULT_ACCESS_VALID_FOR_DAYS,
                "access_scope": ["balances", "transactions", "details"],
            },
        )
        agreement_id = agreement["id"]
        # Track expiry from our own clock; GC's `created` is ISO but using local
        # `now()` is good enough for surfacing "re-auth in N days" — we always
        # re-fetch the requisition on demand.
        expires_at = datetime.now(UTC) + timedelta(days=DEFAULT_ACCESS_VALID_FOR_DAYS)
        payload: dict[str, Any] = {
            "redirect": redirect_uri,
            "institution_id": institution_id,
            "agreement": agreement_id,
            "reference": reference,
        }
        if user_language:
            payload["user_language"] = user_language
        requisition = await self._request("POST", "/requisitions/", json=payload)
        return LinkSession(
            requisition_id=requisition["id"],
            link_url=requisition["link"],
            agreement_id=agreement_id,
            expires_at=expires_at,
        )

    async def complete_link(self, requisition_id: str) -> list[LinkedAccountRef]:
        req = await self._request("GET", f"/requisitions/{requisition_id}/")
        if req.get("status") not in {"LN", "LINKED"} and not req.get("accounts"):
            # GC uses two-letter status codes — LN = linked. Other states (CR/EX/RJ/...)
            # mean the user hasn't completed auth, or the link expired/was rejected.
            raise BankProviderError(
                f"Requisition {requisition_id} is not linked (status={req.get('status')!r})"
            )
        refs: list[LinkedAccountRef] = []
        for account_id in req.get("accounts", []):
            details = await self._safe_account_details(account_id)
            account = details.get("account", {}) if details else {}
            iban = account.get("iban") or ""
            refs.append(
                LinkedAccountRef(
                    external_account_id=account_id,
                    iban_last4=iban[-4:] if iban else None,
                    name=account.get("name") or account.get("ownerName"),
                    currency=account.get("currency"),
                )
            )
        return refs

    async def fetch_transactions(
        self,
        *,
        external_account_id: str,
        date_from: date,
        date_to: date,
    ) -> list[NormalizedTxn]:
        params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
        data = await self._request(
            "GET", f"/accounts/{external_account_id}/transactions/", params=params
        )
        booked = data.get("transactions", {}).get("booked", [])
        out: list[NormalizedTxn] = []
        for t in booked:
            normalized = self._normalize_txn(t)
            if normalized is not None:
                out.append(normalized)
        return out

    async def revoke(self, requisition_id: str) -> None:
        # 404 is fine — the requisition may have already expired or been wiped.
        await self._request("DELETE", f"/requisitions/{requisition_id}/", allow_404=True)

    # ---- Internals --------------------------------------------------------------

    @staticmethod
    def _normalize_txn(t: dict[str, Any]) -> NormalizedTxn | None:
        amount_obj = t.get("transactionAmount") or {}
        try:
            amount = Decimal(str(amount_obj.get("amount")))
        except Exception:
            return None
        posted_str = t.get("bookingDate") or t.get("valueDate")
        if not posted_str:
            return None
        try:
            posted_on = date.fromisoformat(posted_str)
        except ValueError:
            return None
        desc_parts = [
            t.get("remittanceInformationUnstructured"),
            t.get("creditorName"),
            t.get("debtorName"),
            t.get("additionalInformation"),
        ]
        desc = next((p for p in desc_parts if p), None) or "(no description)"
        desc = desc.strip()[:500]
        bank_id = t.get("transactionId") or t.get("internalTransactionId")
        return NormalizedTxn(
            posted_on=posted_on,
            description=desc,
            amount=amount,
            bank_transaction_id=bank_id,
            raw=t,
        )

    async def _safe_account_details(self, account_id: str) -> dict[str, Any] | None:
        """Best-effort fetch of account metadata for naming/currency.

        Some banks/account scopes don't expose /details. A 4xx here should not
        abort the link flow — we just produce a generic LinkedAccountRef.
        """
        try:
            return await self._request("GET", f"/accounts/{account_id}/details/")
        except BankProviderError:
            return None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30.0, transport=self._transport)

    async def _ensure_token(self, client: httpx.AsyncClient) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        resp = await client.post(
            f"{self._base_url}/token/new/",
            json={"secret_id": self._secret_id, "secret_key": self._secret_key},
            timeout=15.0,
        )
        if resp.status_code >= 400:
            # Log status only — body contains the secret echo we just sent.
            log.error("gocardless token mint failed: %s", resp.status_code)
            raise BankProviderError("Failed to authenticate with GoCardless")
        body = resp.json()
        token: str = body["access"]
        ttl = int(body.get("access_expires", 24 * 3600))
        self._access_token = token
        self._access_token_expires_at = now + ttl - _TOKEN_REFRESH_SLACK_SECONDS
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        allow_404: bool = False,
    ) -> Any:
        url = f"{self._base_url}{path}"
        async with self._client() as client:
            token = await self._ensure_token(client)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            resp = await client.request(method, url, json=json, params=params, headers=headers)
            if resp.status_code == 404 and allow_404:
                return {}
            if resp.status_code >= 400:
                # Body can contain account holder names, IBANs, etc. — never log it.
                log.error("gocardless %s %s -> %s", method, path, resp.status_code)
                raise BankProviderError(
                    f"GoCardless {method} {path} failed with {resp.status_code}"
                )
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
