from io import BytesIO

from fastapi.testclient import TestClient


def _account(client: TestClient, headers: dict[str, str]) -> int:
    return client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "A", "type": "checking", "currency": "USD", "opening_balance": "0"},
    ).json()["id"]


def test_preview_returns_parsed_rows_with_suggestions(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    cat = client.post(
        "/api/v1/categories", headers=headers, json={"name": "Coffee", "kind": "expense"}
    ).json()
    client.post(
        "/api/v1/category-rules",
        headers=headers,
        json={"pattern": "starbucks", "category_id": cat["id"]},
    )

    csv = b"Date,Description,Amount\n2024-01-15,Starbucks coffee,-4.50\n2024-01-16,Rent,-1500\n"
    r = client.post(
        "/api/v1/imports/preview",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["rows"]) == 2
    assert body["auto_categorized_count"] == 1
    assert body["duplicate_count"] == 0
    by_desc = {row["description"]: row for row in body["rows"]}
    assert by_desc["Starbucks coffee"]["suggested_category_id"] == cat["id"]
    assert by_desc["Rent"]["suggested_category_id"] is None

    # Preview must not have created transactions yet.
    txns = client.get("/api/v1/transactions", headers=headers).json()
    assert txns == []


def test_preview_then_confirm_with_overrides_and_skip(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    food = client.post(
        "/api/v1/categories", headers=headers, json={"name": "Food", "kind": "expense"}
    ).json()
    rent = client.post(
        "/api/v1/categories", headers=headers, json={"name": "Rent", "kind": "expense"}
    ).json()

    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n2024-01-16,Rent,-1500\n2024-01-17,Junk,-1\n"
    pr = client.post(
        "/api/v1/imports/preview",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    ).json()

    # Assign user categories for two rows, skip the third.
    by_desc = {row["description"]: row for row in pr["rows"]}
    confirm_payload = {
        "rows": [
            {"id": by_desc["Coffee"]["id"], "category_id": food["id"], "skip": False},
            {"id": by_desc["Rent"]["id"], "category_id": rent["id"], "skip": False},
            {"id": by_desc["Junk"]["id"], "skip": True, "category_id": None},
        ]
    }
    r = client.post(
        f"/api/v1/imports/{pr['import_id']}/confirm", headers=headers, json=confirm_payload
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows_imported"] == 2

    txns = client.get("/api/v1/transactions", headers=headers).json()
    txns_by_desc = {t["description"]: t for t in txns}
    assert "Junk" not in txns_by_desc
    assert txns_by_desc["Coffee"]["category_id"] == food["id"]
    assert txns_by_desc["Rent"]["category_id"] == rent["id"]


def test_confirm_rejects_cross_user_category(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers_a, _ = auth_client
    account_id = _account(client, headers_a)

    # User B's category.
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "userc@coffer.dev", "password": "userc-password-1234"},
    )
    headers_b = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    cat_b = client.post(
        "/api/v1/categories", headers=headers_b, json={"name": "B-Food", "kind": "expense"}
    ).json()

    client.cookies.clear()
    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n"
    pr = client.post(
        "/api/v1/imports/preview",
        headers=headers_a,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    ).json()

    client.cookies.clear()
    r = client.post(
        f"/api/v1/imports/{pr['import_id']}/confirm",
        headers=headers_a,
        json={"rows": [{"id": pr["rows"][0]["id"], "category_id": cat_b["id"], "skip": False}]},
    )
    assert r.status_code == 404


def test_discard_preview(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n"
    pr = client.post(
        "/api/v1/imports/preview",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    ).json()
    r = client.delete(f"/api/v1/imports/{pr['import_id']}", headers=headers)
    assert r.status_code == 204

    # Discarded preview cannot be confirmed.
    r = client.post(
        f"/api/v1/imports/{pr['import_id']}/confirm",
        headers=headers,
        json={"rows": []},
    )
    assert r.status_code == 400


def test_confirm_twice_400s(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id = _account(client, headers)
    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n"
    pr = client.post(
        "/api/v1/imports/preview",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    ).json()
    confirm = {"rows": [{"id": pr["rows"][0]["id"], "skip": False, "category_id": None}]}
    r1 = client.post(f"/api/v1/imports/{pr['import_id']}/confirm", headers=headers, json=confirm)
    assert r1.status_code == 200
    r2 = client.post(f"/api/v1/imports/{pr['import_id']}/confirm", headers=headers, json=confirm)
    assert r2.status_code == 400
