"""Opt-in FX feed: provider seam, inversion math, manual-wins, staleness,
failure cooldown, and the explicit refresh endpoint. The HTTP boundary
(fx_feed._http_get) is monkeypatched — no test touches the network."""

from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select

from app.models.fx_rate import FxRate
from app.services import fx_feed

TODAY = date.today()

# The failure cooldown is module-level in-process state — conftest's autouse
# _reset_fx_cooldowns fixture clears it around every test.


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


def _install_feed(monkeypatch, payload=None, exc=None) -> list[str]:
    """Patch the network seam; returns the (mutating) list of requested URLs
    so tests can assert exactly how many outbound calls were attempted."""
    calls: list[str] = []

    def _get(url: str):
        calls.append(url)
        if exc is not None:
            raise exc
        return _FakeResponse(payload)

    monkeypatch.setattr(fx_feed, "_http_get", _get)
    return calls


def _payload(rates: dict) -> dict:
    return {"result": "success", "rates": rates}


def _settings(client, headers, **fields):
    r = client.patch("/api/v1/auth/me", headers=headers, json=fields)
    assert r.status_code == 200, r.text
    return r.json()


def _add_account(client, headers, currency: str) -> None:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": f"acct-{currency}", "type": "checking", "currency": currency},
    )
    assert r.status_code == 201, r.text


def _add_debt(client, headers, currency: str) -> None:
    r = client.post(
        "/api/v1/debts",
        headers=headers,
        json={"name": f"debt-{currency}", "current_balance": "1000", "currency": currency},
    )
    assert r.status_code == 201, r.text


def _rates_by_currency(client, headers) -> dict[str, dict]:
    r = client.get("/api/v1/fx", headers=headers)
    assert r.status_code == 200, r.text
    return {row["currency"]: row for row in r.json()}


# ------------------------------------------------------------ opt-in gating


def test_disabled_default_makes_no_fetch(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    calls = _install_feed(monkeypatch, _payload({"CLP": 1250.0}))
    # In-use foreign currency exists, but the user never opted in.
    _settings(client, headers, display_currency="GBP")
    _add_account(client, headers, "CLP")
    assert client.get("/api/v1/fx", headers=headers).json() == []
    assert calls == []


def test_settings_expose_and_carry_fx_auto_refresh(auth_client) -> None:
    client, headers, _ = auth_client
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["fx_auto_refresh"] is False
    assert _settings(client, headers, fx_auto_refresh=True)["fx_auto_refresh"] is True
    # Strictly bool: explicit null must not reach the NOT NULL column.
    r = client.patch("/api/v1/auth/me", headers=headers, json={"fx_auto_refresh": None})
    assert r.status_code == 422


# ------------------------------------------------- happy path + staleness


def test_enabled_stale_upserts_auto_rows_for_in_use_currencies(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    _add_debt(client, headers, "EUR")
    # USD is in the payload but not in use — it must not create a row.
    calls = _install_feed(monkeypatch, _payload({"CLP": 1250.0, "EUR": 1.25, "USD": 0.8}))

    rows = _rates_by_currency(client, headers)
    assert set(rows) == {"CLP", "EUR"}
    # er-api values are units-per-base (1 GBP = 1250 CLP); Coffer stores the
    # inverse: 1 CLP = 0.0008 GBP.
    assert Decimal(rows["CLP"]["rate"]) == Decimal("0.0008")
    assert Decimal(rows["EUR"]["rate"]) == Decimal("0.8")
    assert all(row["source"] == "auto" for row in rows.values())
    assert all(row["as_of"] == TODAY.isoformat() for row in rows.values())
    assert len(calls) == 1
    assert calls[0].endswith("/GBP")  # quoted against the display currency

    # Fresh (as_of today, all in-use covered) -> a second read fetches nothing.
    _rates_by_currency(client, headers)
    assert len(calls) == 1


def test_auto_rows_older_than_a_day_refetch(auth_client, monkeypatch, db_session) -> None:
    client, headers, user_id = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    db_session.add(
        FxRate(user_id=user_id, currency="CLP", rate=Decimal("0.0009"), as_of=TODAY - timedelta(days=2), source="auto")
    )
    db_session.commit()
    calls = _install_feed(monkeypatch, _payload({"CLP": 1250.0}))

    rows = _rates_by_currency(client, headers)
    assert len(calls) == 1
    assert Decimal(rows["CLP"]["rate"]) == Decimal("0.0008")
    assert rows["CLP"]["as_of"] == TODAY.isoformat()


# ----------------------------------------------------------- manual wins


def test_manual_rate_never_overwritten_and_never_triggers_fetch(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    _add_debt(client, headers, "EUR")
    r = client.put("/api/v1/fx", headers=headers, json=[{"currency": "CLP", "rate": 0.001}])
    assert r.status_code == 200, r.text
    calls = _install_feed(monkeypatch, _payload({"CLP": 1250.0, "EUR": 1.25}))

    # EUR is uncovered -> fetch happens, but the manual CLP row survives it.
    rows = _rates_by_currency(client, headers)
    assert len(calls) == 1
    assert Decimal(rows["CLP"]["rate"]) == Decimal("0.001")
    assert rows["CLP"]["source"] == "manual"
    assert rows["EUR"]["source"] == "auto"

    # PUT over the fetched EUR row flips it to manual...
    r = client.put("/api/v1/fx", headers=headers, json=[{"currency": "EUR", "rate": 0.9}])
    row = next(x for x in r.json() if x["currency"] == "EUR")
    assert row["source"] == "manual"
    assert Decimal(row["rate"]) == Decimal("0.9")

    # ...and manual-only coverage means later reads fetch nothing at all.
    _rates_by_currency(client, headers)
    assert len(calls) == 1


# ------------------------------------------------- failures + cooldown


def test_provider_error_serves_last_known_and_cools_down(auth_client, monkeypatch, db_session) -> None:
    client, headers, user_id = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    stale_day = TODAY - timedelta(days=3)
    db_session.add(FxRate(user_id=user_id, currency="CLP", rate=Decimal("0.0009"), as_of=stale_day, source="auto"))
    db_session.commit()
    calls = _install_feed(monkeypatch, exc=httpx.ConnectError("provider down"))

    # Stale + enabled -> one attempt; failure leaves the last-known row serving.
    rows = _rates_by_currency(client, headers)
    assert len(calls) == 1
    assert Decimal(rows["CLP"]["rate"]) == Decimal("0.0009")
    assert rows["CLP"]["as_of"] == stale_day.isoformat()

    # Within the cooldown no further outbound call is attempted.
    _rates_by_currency(client, headers)
    assert len(calls) == 1


def test_malformed_payload_shape_is_a_fetch_failure(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    calls = _install_feed(monkeypatch, {"result": "error", "error-type": "unknown-code"})

    assert client.get("/api/v1/fx", headers=headers).json() == []
    assert len(calls) == 1
    _rates_by_currency(client, headers)  # cooldown: no second attempt
    assert len(calls) == 1


def test_out_of_range_entry_rejected_others_land(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    for currency in ("GBP", "CLP", "EUR", "JPY"):
        _add_account(client, headers, currency)
    # CLP negative and JPY so tiny its inverse exceeds RATE_MAX: both are
    # per-currency failures — dropped, never persisted, never raised.
    calls = _install_feed(monkeypatch, _payload({"CLP": -1250.0, "EUR": 1.25, "JPY": 1e-11}))

    rows = _rates_by_currency(client, headers)
    assert set(rows) == {"EUR"}
    assert Decimal(rows["EUR"]["rate"]) == Decimal("0.8")
    assert len(calls) == 1


def test_provider_missing_currency_stays_unconverted(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    _add_debt(client, headers, "EUR")
    calls = _install_feed(monkeypatch, _payload({"EUR": 1.25}))

    rows = _rates_by_currency(client, headers)
    assert set(rows) == {"EUR"}  # CLP: no row — downstream shows unconverted
    assert len(calls) == 1


# -------------------------------------------------------- POST /fx/refresh


def test_refresh_endpoint_requires_opt_in(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    calls = _install_feed(monkeypatch, _payload({"CLP": 1250.0}))
    r = client.post("/api/v1/fx/refresh", headers=headers)
    assert r.status_code == 400
    assert calls == []


def test_refresh_endpoint_bypasses_staleness_not_cooldown(auth_client, monkeypatch) -> None:
    client, headers, _ = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    calls = _install_feed(monkeypatch, _payload({"CLP": 1250.0}))

    r = client.post("/api/v1/fx/refresh", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["refreshed_count"] == 1
    assert Decimal(r.json()["rates"][0]["rate"]) == Decimal("0.0008")

    # Rows are fresh (as_of today) — a forced refresh still refetches.
    calls2 = _install_feed(monkeypatch, _payload({"CLP": 1000.0}))
    r = client.post("/api/v1/fx/refresh", headers=headers)
    assert r.json()["refreshed_count"] == 1
    assert Decimal(r.json()["rates"][0]["rate"]) == Decimal("0.001")
    assert len(calls) == 1 and len(calls2) == 1

    # A failure cools down; the next forced refresh makes no outbound call.
    fail_calls = _install_feed(monkeypatch, exc=httpx.ReadTimeout("slow provider"))
    assert client.post("/api/v1/fx/refresh", headers=headers).json()["refreshed_count"] == 0
    assert client.post("/api/v1/fx/refresh", headers=headers).json()["refreshed_count"] == 0
    assert len(fail_calls) == 1
    # Last-known rates keep serving through the outage.
    assert Decimal(_rates_by_currency(client, headers)["CLP"]["rate"]) == Decimal("0.001")


# ------------------------------------------- display-currency change wipe


def test_display_change_deletes_manual_and_auto_rows(auth_client, monkeypatch, db_session) -> None:
    client, headers, user_id = auth_client
    _settings(client, headers, display_currency="GBP", fx_auto_refresh=True)
    _add_account(client, headers, "GBP")
    _add_account(client, headers, "CLP")
    _install_feed(monkeypatch, _payload({"CLP": 1250.0}))
    client.put("/api/v1/fx", headers=headers, json=[{"currency": "EUR", "rate": 0.85}])
    rows = _rates_by_currency(client, headers)
    assert {c: r["source"] for c, r in rows.items()} == {"CLP": "auto", "EUR": "manual"}

    # Rates were quoted against GBP — a new display target wipes them all.
    _settings(client, headers, display_currency="USD")
    remaining = db_session.scalars(select(FxRate).where(FxRate.user_id == user_id)).all()
    assert remaining == []
