"""Login/signup rate limiting.

slowapi runs in-process. That's fine for a single-worker personal deployment.
If we ever scale horizontally, swap the storage backend for Redis via
`limiter._storage = ...` rather than tearing this out.
"""

from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _key_func(request: Request) -> str:
    return get_remote_address(request) or "anonymous"


# In tests we sometimes hit the same endpoint many times in a row; the limiter
# is disabled there to keep the suite honest about the route under test rather
# than the limiter wrapper.
_enabled = os.environ.get("COFFER_ENV", "production") not in {"test"}

limiter = Limiter(key_func=_key_func, enabled=_enabled)
