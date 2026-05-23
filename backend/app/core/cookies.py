"""Session cookie helpers.

Browser auth is a single HttpOnly cookie. API clients (and FastAPI's docs UI)
can still use a Bearer header — see `get_current_user` for the lookup order.
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import settings

COOKIE_NAME = "coffer_session"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        # Same-origin in dev (localhost), strict SSL in production. We can't
        # use Secure on plain-http localhost or browsers reject the cookie.
        secure=settings.coffer_env not in {"dev", "test"},
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
