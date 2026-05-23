from fastapi.testclient import TestClient


def test_signup_login_me(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "alice@example.com", "password": "alice-password-1234"},
    )
    assert r.status_code == 201
    token = r.json()["access_token"]
    assert token

    # OAuth2 password flow is form-encoded
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "alice@example.com", "password": "alice-password-1234"},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"


def test_login_wrong_password(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/signup",
        json={"email": "bob@example.com", "password": "bob-password-1234"},
    )
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "bob@example.com", "password": "wrong"},
    )
    assert r.status_code == 401


def test_signup_duplicate_email(client: TestClient) -> None:
    payload = {"email": "carol@example.com", "password": "carol-password-1234"}
    assert client.post("/api/v1/auth/signup", json=payload).status_code == 201
    assert client.post("/api/v1/auth/signup", json=payload).status_code == 409


def test_me_requires_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
