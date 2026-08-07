from pydantic import BaseModel, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str | None = None
    display_currency: str | None = None
    fx_auto_refresh: bool = False

    model_config = {"from_attributes": True}


class UserSettingsUpdate(BaseModel):
    # Explicit null clears the setting (back to the most-common fallback).
    display_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    # Opt-in FX auto-refresh. Strictly bool (null -> 422): the column is NOT
    # NULL, and exclude_unset keeps an omitted field from touching it.
    fx_auto_refresh: bool = False

    @field_validator("display_currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v is not None else None
