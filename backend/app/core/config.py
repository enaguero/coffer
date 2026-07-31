from pydantic_settings import BaseSettings, SettingsConfigDict

# Defaults that must never reach a non-dev environment. Keep this list in sync
# with placeholders shipped in .env.example and docker-compose.yml.
_FORBIDDEN_JWT_SECRETS = frozenset(
    {"", "change-me", "change-me-in-production", "replace-with-a-long-random-string"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://coffer:coffer@db:5432/coffer"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    cors_origins: str = "http://localhost:5173"
    upload_dir: str = "/app/uploads"
    # Set to "dev" / "test" to allow the placeholder JWT secret. Anything else
    # (including unset) is treated as production and refuses to boot with a weak secret.
    coffer_env: str = "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def assert_production_safe(self) -> None:
        if self.coffer_env in {"dev", "test"}:
            return
        if self.jwt_secret.strip() in _FORBIDDEN_JWT_SECRETS or len(self.jwt_secret) < 32:
            raise RuntimeError(
                "JWT_SECRET is unset, a known placeholder, or shorter than 32 chars. "
                "Set a strong secret in .env, or set COFFER_ENV=dev for local development."
            )


settings = Settings()
settings.assert_production_safe()
