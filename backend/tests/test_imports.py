from io import BytesIO

from fastapi.testclient import TestClient


def _make_account(client: TestClient, headers: dict[str, str]) -> int:
    r = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Checking", "type": "checking", "currency": "USD", "opening_balance": "0"},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_upload_csv_imports_rows_and_dedupes_on_reupload(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id = _make_account(client, headers)
    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n2024-01-16,Payroll,2500.00\n"

    r1 = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("statement.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r1.status_code == 201, r1.text
    body1 = r1.json()
    assert body1["rows_parsed"] == 2
    assert body1["rows_imported"] == 2
    assert body1["skipped_duplicates"] == 0

    # Re-upload: same rows should all dedupe via external_id.
    r2 = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("statement.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r2.status_code == 201, r2.text
    body2 = r2.json()
    assert body2["rows_parsed"] == 2
    assert body2["rows_imported"] == 0
    assert body2["skipped_duplicates"] == 2


def test_upload_rejects_empty_file(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id = _make_account(client, headers)
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("empty.csv", BytesIO(b""), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 400


def test_upload_rejects_unsupported_extension(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id = _make_account(client, headers)
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("statement.txt", BytesIO(b"x"), "text/plain")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 400


def test_upload_rejects_other_users_account(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client_a, headers_a, _ = auth_client
    account_id = _make_account(client_a, headers_a)

    # Sign up user B with fresh credentials on the same client. Clear cookies
    # between calls so the Bearer header is the unambiguous identity — the
    # cookie jar is shared across users in this TestClient, and
    # get_current_user prefers cookie over Bearer.
    client_a.cookies.clear()
    r = client_a.post(
        "/api/v1/auth/signup",
        json={"email": "second@coffer.dev", "password": "second-pw-1234"},
    )
    assert r.status_code == 201
    headers_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n"
    client_a.cookies.clear()
    r = client_a.post(
        "/api/v1/imports/upload",
        headers=headers_b,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},  # A's account
    )
    assert r.status_code == 404
