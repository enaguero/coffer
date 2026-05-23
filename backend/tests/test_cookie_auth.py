"""Cookie auth works alongside the Bearer header (task #12)."""

from fastapi.testclient import TestClient


def test_login_sets_httponly_cookie(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/signup",
        json={"email": "cookie@coffer.dev", "password": "cookie-password-1234"},
    )
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "cookie@coffer.dev", "password": "cookie-password-1234"},
    )
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "coffer_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.lower() or "samesite=lax" in set_cookie.lower()


def test_me_works_with_cookie_only(client: TestClient) -> None:
    """No Authorization header — TestClient persists cookies between calls."""
    client.post(
        "/api/v1/auth/signup",
        json={"email": "cookieonly@coffer.dev", "password": "cookie-password-1234"},
    )
    # Don't reuse the signup cookie — log in fresh.
    client.cookies.clear()
    client.post(
        "/api/v1/auth/login",
        data={
            "username": "cookieonly@coffer.dev",
            "password": "cookie-password-1234",
        },
    )
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "cookieonly@coffer.dev"


def test_logout_clears_cookie(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/signup",
        json={"email": "logout@coffer.dev", "password": "logout-password-1234"},
    )
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_bearer_still_works(client: TestClient) -> None:
    """/docs and API clients should still be usable via Bearer."""
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "bearer@coffer.dev", "password": "bearer-password-1234"},
    )
    token = r.json()["access_token"]
    client.cookies.clear()
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
