from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.ota.models import OTA_CHANNELS, SYNC_TYPES, OTAChannelConfig, OTAListingMapping, OTASyncLog
from app.modules.ota.schemas import (
    ManualSyncRequest,
    OTAChannelConfigOut,
    OTAChannelConfigUpdate,
    OTAListingMappingCreate,
    OTAListingMappingOut,
    OTASyncLogOut,
)
from app.modules.ota.service import run_sync
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/ota", tags=["ota"])


def _ensure_channels_exist(db: Session):
    for ch in OTA_CHANNELS:
        if db.scalar(select(OTAChannelConfig).where(OTAChannelConfig.channel == ch)) is None:
            db.add(OTAChannelConfig(channel=ch))
    db.commit()


@router.get("/channels", response_model=list[OTAChannelConfigOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_channels(db: Session = Depends(get_db)):
    _ensure_channels_exist(db)
    return db.scalars(select(OTAChannelConfig)).all()


@router.patch("/channels/{channel}", response_model=OTAChannelConfigOut, dependencies=[Depends(require_role("admin"))])
def update_channel(channel: str, payload: OTAChannelConfigUpdate, db: Session = Depends(get_db)):
    _ensure_channels_exist(db)
    config = db.scalar(select(OTAChannelConfig).where(OTAChannelConfig.channel == channel))
    if config is None:
        raise HTTPException(404, "Unknown channel")
    if payload.sync_enabled is not None:
        config.sync_enabled = payload.sync_enabled
    if payload.notes is not None:
        config.notes = payload.notes
    db.commit()
    db.refresh(config)
    return config


@router.post("/mappings", response_model=OTAListingMappingOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_mapping(payload: OTAListingMappingCreate, db: Session = Depends(get_db)):
    if payload.channel not in OTA_CHANNELS:
        raise HTTPException(400, f"Invalid channel '{payload.channel}'")
    mapping = OTAListingMapping(**payload.model_dump())
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


@router.get("/mappings", response_model=list[OTAListingMappingOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_mappings(channel: str | None = None, db: Session = Depends(get_db)):
    query = select(OTAListingMapping)
    if channel:
        query = query.where(OTAListingMapping.channel == channel)
    return db.scalars(query).all()


@router.post("/sync", response_model=OTASyncLogOut, dependencies=[Depends(require_role("admin", "staff"))])
def manual_sync(payload: ManualSyncRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The 'Manual Sync Button' — runs the mock adapter (see service.py docstring for why it's mock)."""
    if payload.channel not in OTA_CHANNELS:
        raise HTTPException(400, f"Invalid channel '{payload.channel}'")
    if payload.sync_type not in SYNC_TYPES:
        raise HTTPException(400, f"Invalid sync_type '{payload.sync_type}'")
    return run_sync(db, payload.channel, payload.sync_type, triggered_by=user.id)


@router.get("/sync-logs", response_model=list[OTASyncLogOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_sync_logs(channel: str | None = None, db: Session = Depends(get_db)):
    query = select(OTASyncLog).order_by(OTASyncLog.created_at.desc()).limit(200)
    if channel:
        query = query.where(OTASyncLog.channel == channel)
    return db.scalars(query).all()
