from io import BytesIO

from fastapi.testclient import TestClient


def _seed(client: TestClient, headers: dict[str, str]) -> tuple[int, int]:
    acct = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "A", "type": "checking", "currency": "USD", "opening_balance": "0"},
    ).json()
    cat = client.post(
        "/api/v1/categories", headers=headers, json={"name": "Food", "kind": "expense"}
    ).json()
    csv = b"Date,Description,Amount\n2024-01-15,Coffee,-4.50\n2024-01-16,Lunch,-12\n"
    client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(acct["id"])},
    )
    return acct["id"], cat["id"]


def test_uncategorized_filter(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    _seed(client, headers)
    r = client.get("/api/v1/transactions?uncategorized=true", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(t["category_id"] is None for t in rows)


def test_bulk_assign_updates_all_ids(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    _, cat_id = _seed(client, headers)
    txns = client.get("/api/v1/transactions", headers=headers).json()
    ids = [t["id"] for t in txns]

    r = client.post(
        "/api/v1/transactions/bulk-assign",
        headers=headers,
        json={"transaction_ids": ids, "category_id": cat_id},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    after = client.get("/api/v1/transactions", headers=headers).json()
    assert all(t["category_id"] == cat_id for t in after)


def test_bulk_assign_clear_with_null_category(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    _, cat_id = _seed(client, headers)
    txns = client.get("/api/v1/transactions", headers=headers).json()
    ids = [t["id"] for t in txns]
    client.post(
        "/api/v1/transactions/bulk-assign",
        headers=headers,
        json={"transaction_ids": ids, "category_id": cat_id},
    )
    r = client.post(
        "/api/v1/transactions/bulk-assign",
        headers=headers,
        json={"transaction_ids": ids, "category_id": None},
    )
    assert r.status_code == 200
    after = client.get("/api/v1/transactions", headers=headers).json()
    assert all(t["category_id"] is None for t in after)


def test_bulk_assign_only_touches_callers_rows(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    """User A's bulk-assign with B's transaction ids must be a no-op."""
    client, headers_a, _ = auth_client
    acct_a, cat_a = _seed(client, headers_a)

    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "bulkb@coffer.dev", "password": "bulkb-password-1234"},
    )
    headers_b = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    _seed(client, headers_b)
    client.cookies.clear()
    b_txns = client.get("/api/v1/transactions", headers=headers_b).json()
    b_ids = [t["id"] for t in b_txns]

    client.cookies.clear()
    r = client.post(
        "/api/v1/transactions/bulk-assign",
        headers=headers_a,
        json={"transaction_ids": b_ids, "category_id": cat_a},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 0  # WHERE user_id == A filtered all of B's ids out
