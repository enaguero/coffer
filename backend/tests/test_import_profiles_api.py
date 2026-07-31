"""API tests: /banks catalog, per-account import-profile CRUD, and the preview
flow's preset/profile/inference behavior."""

from io import BytesIO

from fastapi.testclient import TestClient

MONZO_CSV = (
    b"Transaction ID,Date,Time,Type,Name,Emoji,Category,Amount,Currency,"
    b"Local amount,Local currency,Notes and #tags,Address,Receipt,Description,"
    b"Category split,Money Out,Money In\n"
    b"tx_0001,01/03/2026,10:23:45,Card payment,Pret A Manger,,Eating out,-4.50,GBP,"
    b"-4.50,GBP,,London,,PRET A MANGER LONDON,,-4.50,\n"
)

GENERIC_CSV = b"Date,Description,Amount\n2026-03-01,Coffee,-4.50\n2026-03-02,Salary,2500.00\n"

PROFILE_CONFIG = {
    "date_column": "Date",
    "description_columns": ["Description"],
    "amount_column": "Amount",
}


def _account(
    client: TestClient,
    headers: dict[str, str],
    *,
    bank_id: str | None = None,
    type_: str = "checking",
) -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "name": "A",
            "type": type_,
            "currency": "GBP",
            "opening_balance": "0",
            "bank_id": bank_id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _preview(client: TestClient, headers: dict[str, str], account_id: int, content: bytes) -> dict:
    r = client.post(
        "/api/v1/imports/preview",
        headers=headers,
        files={"file": ("s.csv", BytesIO(content), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_banks_catalog_lists_uk_banks(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.get("/api/v1/banks", headers=headers)
    assert r.status_code == 200, r.text
    banks = {b["id"]: b for b in r.json()}
    assert "monzo" in banks and "lloyds" in banks
    assert "credit_card" in banks["lloyds"]["account_types"]
    # Unauthenticated (no bearer header, session cookie cleared) → 401.
    client.cookies.clear()
    assert client.get("/api/v1/banks").status_code == 401


def test_account_rejects_unknown_bank_id(auth_client) -> None:
    client, headers, _ = auth_client
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "A", "type": "checking", "currency": "GBP", "bank_id": "not-a-bank"},
    )
    assert r.status_code == 422


def test_import_profile_crud(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)

    assert client.get(f"/api/v1/accounts/{account_id}/import-profile", headers=headers).status_code == 404

    r = client.put(
        f"/api/v1/accounts/{account_id}/import-profile",
        headers=headers,
        json={"name": "My bank", "source": "custom", "config": PROFILE_CONFIG},
    )
    assert r.status_code == 200, r.text
    assert r.json()["config"]["date_column"] == "Date"

    # Upsert overwrites in place.
    r = client.put(
        f"/api/v1/accounts/{account_id}/import-profile",
        headers=headers,
        json={"name": "Renamed", "config": {**PROFILE_CONFIG, "invert_amount": True}},
    )
    assert r.status_code == 200
    got = client.get(f"/api/v1/accounts/{account_id}/import-profile", headers=headers).json()
    assert got["name"] == "Renamed"
    assert got["config"]["invert_amount"] is True

    assert client.delete(f"/api/v1/accounts/{account_id}/import-profile", headers=headers).status_code == 204
    assert client.get(f"/api/v1/accounts/{account_id}/import-profile", headers=headers).status_code == 404


def test_import_profile_config_validation(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    # No amount source at all → rejected by ImportProfileConfig's validator.
    r = client.put(
        f"/api/v1/accounts/{account_id}/import-profile",
        headers=headers,
        json={"config": {"date_column": "Date", "description_columns": ["Description"]}},
    )
    assert r.status_code == 422


def test_import_profile_is_owner_scoped(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)

    r = client.post("/api/v1/auth/signup", json={"email": "other@coffer.dev", "password": "other-password-1"})
    other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.put(
        f"/api/v1/accounts/{account_id}/import-profile",
        headers=other_headers,
        json={"config": PROFILE_CONFIG},
    )
    assert r.status_code == 404


def test_preview_uses_bank_preset_and_reports_source(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers, bank_id="monzo")

    body = _preview(client, headers, account_id, MONZO_CSV)
    assert body["source"] == "preset:monzo"
    assert body["warnings"] == []
    assert body["has_profile"] is False
    row = body["rows"][0]
    assert row["description"] == "Pret A Manger"
    assert row["amount"] == "-4.50"
    assert row["external_id"] == "tx_0001"  # Monzo's own transaction id


def test_preview_reports_inferred_config_and_then_uses_saved_profile(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)

    body = _preview(client, headers, account_id, GENERIC_CSV)
    assert body["source"] == "heuristic"
    inferred = body["inferred_config"]
    assert inferred is not None

    r = client.put(
        f"/api/v1/accounts/{account_id}/import-profile",
        headers=headers,
        json={"name": "Saved", "source": "inferred", "config": inferred},
    )
    assert r.status_code == 200, r.text

    body = _preview(client, headers, account_id, GENERIC_CSV)
    assert body["source"] == "profile"
    assert body["has_profile"] is True
    assert body["inferred_config"] is None
    assert len(body["rows"]) == 2
    # Same file previewed twice → second pass flags everything as duplicate only
    # after commit; previews alone must not create transactions.
    assert client.get("/api/v1/transactions", headers=headers).json() == []


def test_upload_ofx_end_to_end(auth_client) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    ofx = (
        b"OFXHEADER:100\n\n<OFX><BANKTRANLIST>"
        b"<STMTTRN><DTPOSTED>20260301<TRNAMT>-4.50<FITID>abc123<NAME>PRET</STMTTRN>"
        b"</BANKTRANLIST></OFX>"
    )
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.ofx", BytesIO(ofx), "application/x-ofx")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text
    assert r.json()["rows_imported"] == 1
    txns = client.get("/api/v1/transactions", headers=headers).json()
    assert txns[0]["external_id"] == "abc123"
