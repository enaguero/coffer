# Import for side effect: each module registers itself into the registry.
from app.services.import_engine.adapters import revolut  # noqa: E402,F401
from app.services.import_engine.adapters.base import get_adapter, register_adapter

__all__ = ["get_adapter", "register_adapter"]
