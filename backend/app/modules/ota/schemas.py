import uuid
from datetime import datetime

from pydantic import BaseModel


class OTAChannelConfigOut(BaseModel):
    id: uuid.UUID
    channel: str
    sync_enabled: bool
    is_live: bool
    notes: str | None

    class Config:
        from_attributes = True


class OTAChannelConfigUpdate(BaseModel):
    sync_enabled: bool | None = None
    notes: str | None = None


class OTAListingMappingCreate(BaseModel):
    channel: str
    unit_id: uuid.UUID
    external_listing_id: str


class OTAListingMappingOut(OTAListingMappingCreate):
    id: uuid.UUID

    class Config:
        from_attributes = True


class OTASyncLogOut(BaseModel):
    id: uuid.UUID
    channel: str
    sync_type: str
    status: str
    message: str | None
    records_affected: int
    created_at: datetime

    class Config:
        from_attributes = True


class ManualSyncRequest(BaseModel):
    channel: str
    sync_type: str
