"""Symmetric encryption helper for at-rest secrets.

Not used by the GoCardless path itself — GoCardless's per-user identifiers are
opaque references, useless without the app-level SECRET_ID/SECRET_KEY. This
module is forward-looking for providers (e.g. Plaid) whose per-user
`access_token` *is* a bearer credential and so must not sit in the DB as
plaintext.

Key management: a single 32-byte url-safe base64 Fernet key from
`settings.encryption_key`. Rotation is not implemented — when we need it, swap
to MultiFernet with old keys appended.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class EncryptionNotConfigured(RuntimeError):
    """Raised when encrypt/decrypt is called but ENCRYPTION_KEY is unset."""


def _fernet() -> Fernet:
    if not settings.encryption_key:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEY is not set. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
        )
    return Fernet(settings.encryption_key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt — wrong key or corrupted ciphertext") from exc
