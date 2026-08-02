"""Loading an account's saved import profile as a validated config."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_profile import ImportProfile
from app.services.import_engine.profile import ImportProfileConfig


def load_profile_config(db: Session, account_id: int) -> ImportProfileConfig | None:
    profile = db.scalar(select(ImportProfile).where(ImportProfile.account_id == account_id))
    if profile is None:
        return None
    try:
        return ImportProfileConfig.model_validate(profile.config)
    except ValueError:
        # A profile saved by an older build may no longer validate; ignore it
        # rather than block imports.
        return None
