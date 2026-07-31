"""UK bank catalog — read-only reference data for the account picker."""

from fastapi import APIRouter

from app.core.deps import CurrentUser
from app.schemas.bank import UkBankOut
from app.services.import_engine.catalog import UK_BANKS

router = APIRouter(prefix="/banks", tags=["banks"])


@router.get("", response_model=list[UkBankOut])
def list_banks(current: CurrentUser) -> list[UkBankOut]:
    return [
        UkBankOut(
            id=bank.id,
            name=bank.name,
            account_types=list(bank.account_types),
            formats=list(bank.formats),
            notes=bank.notes,
        )
        for bank in sorted(UK_BANKS, key=lambda b: b.name.lower())
    ]
