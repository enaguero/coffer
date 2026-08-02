"""Household mode: create/join via invites, membership rules, and the
read-only shared-accounts view."""

from datetime import UTC, datetime

from app.models.household import HouseholdInvite


def _signup(client, email: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": "household-pw-1234"})
    assert r.status_code == 201, r.text
    # Signup sets a session cookie, and cookie auth outranks the Bearer
    # header — clear it so each request's Authorization header decides who
    # is calling.
    client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_household(client, headers, name="The Aguero House") -> dict:
    r = client.post("/api/v1/household", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _invite(client, headers) -> str:
    r = client.post("/api/v1/household/invites", headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _join(client, headers, token: str) -> dict:
    r = client.post("/api/v1/household/join", headers=headers, json={"token": token})
    assert r.status_code == 200, r.text
    return r.json()


def _account(client, headers, *, name: str, visibility: str = "private", opening: str = "100") -> int:
    r = client.post(
        "/api/v1/accounts", headers=headers,
        json={"name": name, "type": "checking", "currency": "GBP", "opening_balance": opening,
              "visibility": visibility},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_household_lifecycle(auth_client) -> None:
    client, owner, _ = auth_client
    assert client.get("/api/v1/household", headers=owner).json() is None

    h = _create_household(client, owner)
    assert h["my_role"] == "owner"
    assert len(h["members"]) == 1 and h["members"][0]["is_me"]

    # No second household for the same user.
    assert client.post("/api/v1/household", headers=owner, json={"name": "Another"}).status_code == 400

    partner = _signup(client, "partner@coffer.dev")
    token = _invite(client, owner)
    joined = _join(client, partner, token)
    assert joined["my_role"] == "member"
    assert len(joined["members"]) == 2

    # The token is single-use.
    third = _signup(client, "third@coffer.dev")
    assert client.post("/api/v1/household/join", headers=third, json={"token": token}).status_code == 404


def test_join_rejects_expired_and_unknown_tokens(auth_client, db_session) -> None:
    client, owner, _ = auth_client
    _create_household(client, owner)
    token = _invite(client, owner)
    invite = db_session.scalar(
        HouseholdInvite.__table__.select().where(HouseholdInvite.token == token)
    )
    assert invite is not None
    db_session.execute(
        HouseholdInvite.__table__.update()
        .where(HouseholdInvite.token == token)
        .values(expires_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    db_session.flush()

    joiner = _signup(client, "late@coffer.dev")
    assert client.post("/api/v1/household/join", headers=joiner, json={"token": token}).status_code == 404
    assert client.post("/api/v1/household/join", headers=joiner, json={"token": "x" * 32}).status_code == 404


def test_only_owner_invites_and_removes(auth_client) -> None:
    client, owner, _ = auth_client
    _create_household(client, owner)
    partner = _signup(client, "partner2@coffer.dev")
    _join(client, partner, _invite(client, owner))

    assert client.post("/api/v1/household/invites", headers=partner).status_code == 403

    third = _signup(client, "third2@coffer.dev")
    _join(client, third, _invite(client, owner))
    third_id = client.get("/api/v1/auth/me", headers=third).json()["id"]
    # A member can't remove another member; the owner can.
    assert client.delete(f"/api/v1/household/members/{third_id}", headers=partner).status_code == 403
    assert client.delete(f"/api/v1/household/members/{third_id}", headers=owner).status_code == 204
    assert len(client.get("/api/v1/household", headers=owner).json()["members"]) == 2


def test_leaving_transfers_ownership_and_last_leaver_deletes(auth_client) -> None:
    client, owner, owner_id = auth_client
    _create_household(client, owner)
    partner = _signup(client, "partner3@coffer.dev")
    _join(client, partner, _invite(client, owner))

    # Owner leaves: the remaining member inherits the household.
    assert client.delete(f"/api/v1/household/members/{owner_id}", headers=owner).status_code == 204
    assert client.get("/api/v1/household", headers=owner).json() is None
    h = client.get("/api/v1/household", headers=partner).json()
    assert h["my_role"] == "owner"

    partner_id = next(m["user_id"] for m in h["members"] if m["is_me"])
    assert client.delete(f"/api/v1/household/members/{partner_id}", headers=partner).status_code == 204
    assert client.get("/api/v1/household", headers=partner).json() is None


def test_shared_view_is_visibility_scoped(auth_client) -> None:
    client, owner, _ = auth_client
    _create_household(client, owner)
    partner = _signup(client, "partner4@coffer.dev")
    _join(client, partner, _invite(client, owner))

    _account(client, owner, name="Owner Private", visibility="private", opening="1000")
    _account(client, owner, name="Owner Shared", visibility="household", opening="250")
    _account(client, partner, name="Partner Shared", visibility="household", opening="75.50")

    view = client.get("/api/v1/household/shared", headers=partner).json()
    names = {a["name"] for a in view["accounts"]}
    assert names == {"Owner Shared", "Partner Shared"}  # the private account never appears
    assert view["totals"] == [{"currency": "GBP", "total": "325.50"}]

    owner_shared = next(a for a in view["accounts"] if a["name"] == "Owner Shared")
    assert owner_shared["balance"] == "250.00"
    # Balances only — the shared payload must never carry transactions.
    assert "transactions" not in owner_shared


def test_shared_view_requires_membership_and_stays_isolated(auth_client) -> None:
    client, owner, _ = auth_client
    _create_household(client, owner)
    _account(client, owner, name="Owner Shared", visibility="household")

    outsider = _signup(client, "outsider@coffer.dev")
    assert client.get("/api/v1/household/shared", headers=outsider).status_code == 404

    # A different household must not see this one's shared accounts.
    _create_household(client, outsider, name="Other House")
    view = client.get("/api/v1/household/shared", headers=outsider).json()
    assert view["accounts"] == []


def test_visibility_is_editable_and_validated(auth_client) -> None:
    client, owner, _ = auth_client
    account_id = _account(client, owner, name="Mine")
    r = client.patch(f"/api/v1/accounts/{account_id}", headers=owner, json={"visibility": "household"})
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "household"
    assert (
        client.patch(f"/api/v1/accounts/{account_id}", headers=owner, json={"visibility": "public"}).status_code
        == 422
    )
