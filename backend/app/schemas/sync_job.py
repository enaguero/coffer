from datetime import datetime

from pydantic import BaseModel

from app.models.sync_job import SyncJobStatus


class SyncJobOut(BaseModel):
    id: int
    bank_connection_id: int
    account_id: int | None
    started_at: datetime
    completed_at: datetime | None
    status: SyncJobStatus
    transactions_fetched: int
    transactions_imported: int
    error_message: str | None

    model_config = {"from_attributes": True}


class SyncResponse(BaseModel):
    """Returned immediately from POST /bank-connections/{id}/sync. The actual
    sync runs in the background and updates the SyncJob row asynchronously."""

    sync_job_ids: list[int]
    queued: int
