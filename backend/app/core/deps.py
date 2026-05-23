from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.cookies import COOKIE_NAME
from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User

# auto_error=False so a missing Authorization header isn't an immediate 401 —
# we want to fall through to the cookie before deciding the request is anon.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_current_user(
    request: Request,
    bearer_token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Browser sessions use the HttpOnly cookie; API/docs callers use Bearer.
    token = request.cookies.get(COOKIE_NAME) or bearer_token
    if not token:
        raise credentials_exception
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except ValueError as e:
        raise credentials_exception from e

    user = db.get(User, int(user_id))
    if user is None:
        raise credentials_exception
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]
