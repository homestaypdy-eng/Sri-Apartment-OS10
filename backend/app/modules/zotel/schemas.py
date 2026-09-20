import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ZotelConfigStatus(BaseModel):
    configured: bool
    base_url_set: bool
    hotel_id_set: bool
    api_key_set: bool
    api_secret_set: bool


class ZotelCheckResult(BaseModel):
    category: str
    passed: bool
    message: str


class ZotelConnectivityReport(BaseModel):
    configured: bool
    checks: list[ZotelCheckResult]
    overall_passed: bool


class ZotelRoomMappingCreate(BaseModel):
    unit_id: uuid.UUID
    zotel_room_type_id: str
    zotel_room_type_name: str | None = None


class ZotelRoomMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_id: uuid.UUID
    zotel_room_type_id: str
    zotel_room_type_name: str | None
    created_at: datetime


class ZotelSyncRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_reservation_id: str
    outcome: str
    booking_id: uuid.UUID | None
    detail: str | None
    created_at: datetime


class ZotelSyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sync_type: str
    status: str
    message: str | None
    records_fetched: int
    records_imported: int
    records_updated: int
    records_skipped: int
    records_unmapped: int
    records_conflicted: int
    records_failed: int
    created_at: datetime


class ZotelSyncLogDetail(ZotelSyncLogOut):
    records: list[ZotelSyncRecordOut]


class ZotelUnmappedRoom(BaseModel):
    zotel_room_type_id: str
    zotel_room_type_name: str | None
    first_seen_in_reservation: str


class ZotelReconciliationEntry(BaseModel):
    external_reservation_id: str
    category: str  # matched/missing_in_os10/missing_in_zotel/duplicate/unmapped/conflicting/cancelled_mismatch
    detail: str


class ZotelReconciliationReport(BaseModel):
    generated_at: datetime
    entries: list[ZotelReconciliationEntry]
    summary: dict[str, int]
