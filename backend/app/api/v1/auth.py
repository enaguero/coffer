from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import delete, select

from app.core.cookies import clear_session_cookie, set_session_cookie
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import limiter
from app.core.security import create_access_token, hash_password, verify_password
from app.models.fx_rate import FxRate
from app.models.user import User
from app.schemas.auth import SignupRequest, TokenResponse, UserOut, UserSettingsUpdate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
def signup(
    request: Request, response: Response, payload: SignupRequest, db: DbSession
) -> TokenResponse:
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id)
    set_session_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    response: Response,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> TokenResponse:
    # FastAPI's OAuth2PasswordRequestForm provides .username and .password
    user = db.scalar(select(User).where(User.email == form.username))
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user.id)
    set_session_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
def me(current: CurrentUser) -> User:
    return current


@router.patch("/me", response_model=UserOut)
def update_me(payload: UserSettingsUpdate, current: CurrentUser, db: DbSession) -> User:
    data = payload.model_dump(exclude_unset=True)
    if "display_currency" in data and data["display_currency"] != current.display_currency:
        # Saved rates mean "1 unit = X of the OLD display currency" — reusing
        # them against a new target would silently corrupt every converted
        # total, so a display change wipes them (the UI says so).
        db.execute(delete(FxRate).where(FxRate.user_id == current.id))
    for key, value in data.items():
        setattr(current, key, value)
    db.commit()
    db.refresh(current)
    return current
