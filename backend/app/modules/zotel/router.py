import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.zotel import client, service
from app.modules.zotel.models import ZotelRoomMapping, ZotelSyncLog, ZotelSyncRecord
from app.modules.zotel.schemas import (
    ZotelConfigStatus,
    ZotelConnectivityReport,
    ZotelReconciliationReport,
    ZotelRoomMappingCreate,
    ZotelRoomMappingOut,
    ZotelSyncLogDetail,
    ZotelSyncLogOut,
)
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/zotel", tags=["zotel"])


@router.get("/status", response_model=ZotelConfigStatus, dependencies=[Depends(require_role("admin", "manager", "staff"))])
def get_config_status():
    """Whether Zotel env vars are set — booleans only, never the values."""
    from app.core.config import settings

    return ZotelConfigStatus(
        configured=client.is_configured(),
        base_url_set=bool(settings.zotel_base_url),
        hotel_id_set=bool(settings.zotel_hotel_id),
        api_key_set=bool(settings.zotel_api_key),
        api_secret_set=bool(settings.zotel_api_secret),
    )


@router.post("/connectivity-test", response_model=ZotelConnectivityReport, dependencies=[Depends(require_role("admin"))])
def run_connectivity_test(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return service.check_connectivity(db, triggered_by=current_user.id)


@router.get("/mappings", response_model=list[ZotelRoomMappingOut], dependencies=[Depends(require_role("admin", "manager", "staff"))])
def list_mappings(db: Session = Depends(get_db)):
    return db.scalars(select(ZotelRoomMapping)).all()


@router.post("/mappings", response_model=ZotelRoomMappingOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_mapping(payload: ZotelRoomMappingCreate, db: Session = Depends(get_db)):
    existing_by_unit = db.scalar(select(ZotelRoomMapping).where(ZotelRoomMapping.unit_id == payload.unit_id))
    if existing_by_unit is not None:
        raise HTTPException(400, "This OS10 unit is already mapped to a different Zotel room type")
    existing_by_room = db.scalar(select(ZotelRoomMapping).where(ZotelRoomMapping.zotel_room_type_id == payload.zotel_room_type_id))
    if existing_by_room is not None:
        raise HTTPException(400, "This Zotel room type is already mapped to a different OS10 unit")

    mapping = ZotelRoomMapping(unit_id=payload.unit_id, zotel_room_type_id=payload.zotel_room_type_id, zotel_room_type_name=payload.zotel_room_type_name)
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


@router.delete("/mappings/{mapping_id}", status_code=204, dependencies=[Depends(require_role("admin"))])
def delete_mapping(mapping_id: uuid.UUID, db: Session = Depends(get_db)):
    mapping = db.get(ZotelRoomMapping, mapping_id)
    if mapping is None:
        raise HTTPException(404, "Mapping not found")
    db.delete(mapping)
    db.commit()


@router.post("/sync/bookings", response_model=ZotelSyncLogOut, dependencies=[Depends(require_role("admin"))])
def trigger_bookings_sync(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return service.sync_bookings(db, triggered_by=current_user.id)


@router.get("/sync/logs", response_model=list[ZotelSyncLogOut], dependencies=[Depends(require_role("admin", "manager", "staff"))])
def list_sync_logs(db: Session = Depends(get_db), limit: int = 50):
    return db.scalars(select(ZotelSyncLog).order_by(ZotelSyncLog.created_at.desc()).limit(limit)).all()


@router.get("/sync/logs/{sync_log_id}", response_model=ZotelSyncLogDetail, dependencies=[Depends(require_role("admin", "manager", "staff"))])
def get_sync_log_detail(sync_log_id: uuid.UUID, db: Session = Depends(get_db)):
    sync_log = db.get(ZotelSyncLog, sync_log_id)
    if sync_log is None:
        raise HTTPException(404, "Sync log not found")
    records = db.scalars(select(ZotelSyncRecord).where(ZotelSyncRecord.sync_log_id == sync_log_id)).all()
    return ZotelSyncLogDetail(**ZotelSyncLogOut.model_validate(sync_log).model_dump(), records=records)


@router.get("/reconciliation", response_model=ZotelReconciliationReport, dependencies=[Depends(require_role("admin", "manager"))])
def get_reconciliation_report(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return service.run_reconciliation(db, triggered_by=current_user.id)


@router.get("/unmapped", dependencies=[Depends(require_role("admin", "manager", "staff"))])
def list_unmapped_rooms(db: Session = Depends(get_db)):
    """
    Distinct Zotel room_type_ids seen in ZotelSyncRecord rows with
    outcome='unmapped' but never resolved by a later mapping — surfaced
    from sync history, since Zotel has no dedicated "list room types"
    endpoint in the documented API (room types are only ever seen inside
    availability/prices/booking responses).
    """
    unmapped_records = db.scalars(
        select(ZotelSyncRecord).where(ZotelSyncRecord.outcome == "unmapped").order_by(ZotelSyncRecord.created_at.desc())
    ).all()
    mapped_room_ids = {m.zotel_room_type_id for m in db.scalars(select(ZotelRoomMapping)).all()}
    seen: dict[str, str] = {}
    for rec in unmapped_records:
        room_id = (rec.detail or "").split("room_type_id=")[-1].strip() if rec.detail else None
        if room_id and room_id not in mapped_room_ids and room_id not in seen:
            seen[room_id] = rec.external_reservation_id
    return [{"zotel_room_type_id": rid, "first_seen_in_reservation": txn} for rid, txn in seen.items()]
