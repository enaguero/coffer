from io import BytesIO

from fastapi.testclient import TestClient


def _setup(client: TestClient, headers: dict[str, str]) -> tuple[int, int]:
    """Create an account + a 'Coffee' category, return (account_id, category_id)."""
    acct = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "A", "type": "checking", "currency": "USD", "opening_balance": "0"},
    ).json()
    cat = client.post(
        "/api/v1/categories",
        headers=headers,
        json={"name": "Coffee", "kind": "expense"},
    ).json()
    return acct["id"], cat["id"]


def test_rule_assigns_category_during_import(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id, category_id = _setup(client, headers)
    r = client.post(
        "/api/v1/category-rules",
        headers=headers,
        json={"pattern": "starbucks", "category_id": category_id, "priority": 10},
    )
    assert r.status_code == 201

    csv = b"Date,Description,Amount\n2024-01-15,Starbucks coffee,-4.50\n2024-01-16,Rent,-1500\n"
    r = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["rows_imported"] == 2
    assert body["auto_categorized"] == 1

    txns = client.get("/api/v1/transactions", headers=headers).json()
    by_desc = {t["description"]: t for t in txns}
    assert by_desc["Starbucks coffee"]["category_id"] == category_id
    assert by_desc["Rent"]["category_id"] is None


def test_rule_priority_lower_wins(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id, coffee_id = _setup(client, headers)
    food_id = client.post(
        "/api/v1/categories", headers=headers, json={"name": "Food", "kind": "expense"}
    ).json()["id"]

    # Both rules match "Starbucks coffee" — Coffee has lower priority and should win.
    client.post(
        "/api/v1/category-rules",
        headers=headers,
        json={"pattern": "starbucks", "category_id": coffee_id, "priority": 5},
    )
    client.post(
        "/api/v1/category-rules",
        headers=headers,
        json={"pattern": "coffee", "category_id": food_id, "priority": 50},
    )

    csv = b"Date,Description,Amount\n2024-01-15,Starbucks coffee,-4.50\n"
    client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    )
    txns = client.get("/api/v1/transactions", headers=headers).json()
    assert txns[0]["category_id"] == coffee_id


def test_apply_rules_backfills_uncategorized(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    account_id, category_id = _setup(client, headers)

    # Import without rules → uncategorized.
    csv = b"Date,Description,Amount\n2024-01-15,Starbucks coffee,-4.50\n"
    client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("s.csv", BytesIO(csv), "text/csv")},
        data={"account_id": str(account_id)},
    )
    txns = client.get("/api/v1/transactions", headers=headers).json()
    assert txns[0]["category_id"] is None

    # Add rule, then catch up.
    client.post(
        "/api/v1/category-rules",
        headers=headers,
        json={"pattern": "starbucks", "category_id": category_id},
    )
    r = client.post("/api/v1/category-rules/apply", headers=headers)
    assert r.status_code == 200
    assert r.json()["transactions_updated"] == 1

    txns = client.get("/api/v1/transactions", headers=headers).json()
    assert txns[0]["category_id"] == category_id


def test_rule_with_wrong_user_category_404s(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers_a, _ = auth_client
    # Sign up user B. This replaces the cookie in the shared jar — we clear
    # cookies before each auth-sensitive call so the Bearer header is the only
    # identity in play. Without this, get_current_user prefers the cookie and
    # we'd authenticate as the wrong user.
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "userb@coffer.dev", "password": "userb-password-1234"},
    )
    headers_b = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    cat_b = client.post(
        "/api/v1/categories", headers=headers_b, json={"name": "B-cat", "kind": "expense"}
    ).json()
    # User A tries to attach a rule to user B's category.
    client.cookies.clear()
    r = client.post(
        "/api/v1/category-rules",
        headers=headers_a,
        json={"pattern": "x", "category_id": cat_b["id"]},
    )
    assert r.status_code == 404


def test_duplicate_pattern_409s(
    auth_client: tuple[TestClient, dict[str, str], int],
) -> None:
    client, headers, _ = auth_client
    _, cat_id = _setup(client, headers)
    payload = {"pattern": "amazon", "category_id": cat_id}
    assert client.post("/api/v1/category-rules", headers=headers, json=payload).status_code == 201
    assert client.post("/api/v1/category-rules", headers=headers, json=payload).status_code == 409
