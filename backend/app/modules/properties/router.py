import os
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log
from app.modules.auth.models import User
from app.modules.bookings.schemas import UnitCreate, UnitOut, UnitUpdate
from app.modules.properties.models import LongStayDiscountTier, Property, PropertyFile, SeasonalRate, Unit, UnitPhoto
from app.modules.properties.pricing import calculate_price
from app.modules.properties.schemas import (
    LongStayDiscountTierCreate,
    LongStayDiscountTierOut,
    PriceQuoteOut,
    PropertyCreate,
    PropertyFileOut,
    PropertyOut,
    PropertyUpdate,
    SeasonalRateCreate,
    SeasonalRateOut,
    UnitPhotoOut,
)
from app.modules.notifications.service import trigger_property_created
from app.shared.file_upload import read_and_validate_upload, IMAGE_CONTENT_TYPES
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1", tags=["properties"])


@router.post("/properties", response_model=PropertyOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_property(payload: PropertyCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prop = Property(**payload.model_dump())
    db.add(prop)
    db.flush()
    record_audit_log(db, user.id, "property.create", entity_type="property", entity_id=str(prop.id), details=payload.name)
    trigger_property_created(db, prop)
    db.commit()
    db.refresh(prop)
    return prop


@router.patch("/properties/{property_id}", response_model=PropertyOut, dependencies=[Depends(require_role("admin"))])
def update_property(property_id: uuid.UUID, payload: PropertyUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prop = db.get(Property, property_id)
    if prop is None:
        raise HTTPException(404, "Property not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update were provided")
    for field, value in changes.items():
        setattr(prop, field, value)
    record_audit_log(db, user.id, "property.update", entity_type="property", entity_id=str(prop.id), details=f"Updated: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(prop)
    return prop


@router.patch("/properties/{property_id}/deactivate", response_model=PropertyOut, dependencies=[Depends(require_role("admin"))])
def deactivate_property(property_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Soft-deactivation — the property row, its units, and every booking/
    payment/invoice history under them are untouched. A deactivated
    property is simply excluded from new-booking pickers/availability
    (see bookings/router.py) and shown with an inactive badge in the UI.
    """
    prop = db.get(Property, property_id)
    if prop is None:
        raise HTTPException(404, "Property not found")
    if not prop.is_active:
        raise HTTPException(409, "Property is already deactivated")
    prop.is_active = False
    record_audit_log(db, user.id, "property.deactivate", entity_type="property", entity_id=str(prop.id), details=f"Deactivated: {prop.name}")
    db.commit()
    db.refresh(prop)
    return prop


@router.patch("/properties/{property_id}/reactivate", response_model=PropertyOut, dependencies=[Depends(require_role("admin"))])
def reactivate_property(property_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prop = db.get(Property, property_id)
    if prop is None:
        raise HTTPException(404, "Property not found")
    if prop.is_active:
        raise HTTPException(409, "Property is already active")
    prop.is_active = True
    record_audit_log(db, user.id, "property.reactivate", entity_type="property", entity_id=str(prop.id), details=f"Reactivated: {prop.name}")
    db.commit()
    db.refresh(prop)
    return prop


PROPERTY_FILE_STORAGE_DIR = os.environ.get("PROPERTY_FILE_STORAGE_DIR", "/tmp/sri_apartment_property_files")
os.makedirs(PROPERTY_FILE_STORAGE_DIR, exist_ok=True)


@router.post("/properties/{property_id}/files", response_model=PropertyFileOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
async def upload_property_file(
    property_id: uuid.UUID, file_type: str = "document", caption: str | None = None,
    file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    prop = db.get(Property, property_id)
    if prop is None:
        raise HTTPException(404, "Property not found")
    if file_type not in ("document", "photo"):
        raise HTTPException(400, "file_type must be 'document' or 'photo'")

    allowed_types = IMAGE_CONTENT_TYPES if file_type == "photo" else None
    contents = await read_and_validate_upload(file, allowed_content_types=allowed_types)
    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{property_id}_{file_type}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(PROPERTY_FILE_STORAGE_DIR, stored_name)
    with open(stored_path, "wb") as f:
        f.write(contents)

    record = PropertyFile(
        property_id=property_id, file_type=file_type, filename=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream", file_path=stored_path,
        caption=caption, uploaded_by=user.id,
    )
    db.add(record)
    db.flush()
    record_audit_log(db, user.id, "property.file_upload", entity_type="property", entity_id=str(property_id), details=f"{file_type}: {record.filename}")
    db.commit()
    db.refresh(record)
    return record


@router.get("/properties/{property_id}/files", response_model=list[PropertyFileOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_property_files(property_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(PropertyFile).where(PropertyFile.property_id == property_id)).all()


@router.get("/properties/{property_id}/files/{file_id}/download", dependencies=[Depends(require_role("admin", "staff"))])
def download_property_file(property_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db)):
    record = db.get(PropertyFile, file_id)
    if record is None or record.property_id != property_id:
        raise HTTPException(404, "File not found")
    if not os.path.exists(record.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(record.file_path, media_type=record.content_type, filename=record.filename)


@router.delete("/properties/{property_id}/files/{file_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff"))])
def delete_property_file(property_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    record = db.get(PropertyFile, file_id)
    if record is None or record.property_id != property_id:
        raise HTTPException(404, "File not found")
    record_audit_log(db, user.id, "property.file_delete", entity_type="property", entity_id=str(property_id), details=f"Deleted: {record.filename}")
    if os.path.exists(record.file_path):
        os.remove(record.file_path)
    db.delete(record)
    db.commit()


@router.get("/properties", response_model=list[PropertyOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_properties(db: Session = Depends(get_db)):
    return db.query(Property).all()


@router.post("/units", response_model=UnitOut, status_code=201, dependencies=[Depends(require_role("admin"))])
def create_unit(payload: UnitCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.get(Property, payload.property_id) is None:
        raise HTTPException(404, "Property not found")
    unit = Unit(**payload.model_dump())
    db.add(unit)
    db.flush()
    record_audit_log(db, user.id, "unit.create", entity_type="unit", entity_id=str(unit.id), details=f"{payload.name}, base rate Rs.{payload.base_rate}")
    db.commit()
    db.refresh(unit)
    return unit


@router.get("/units", response_model=list[UnitOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_units(db: Session = Depends(get_db)):
    return db.query(Unit).all()


@router.patch("/units/{unit_id}", response_model=UnitOut, dependencies=[Depends(require_role("admin"))])
def update_unit(unit_id: uuid.UUID, payload: UnitUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(unit, field, value)
    if changes:
        record_audit_log(db, user.id, "unit.update", entity_type="unit", entity_id=str(unit.id), details=f"Updated: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(unit)
    return unit


@router.patch("/units/{unit_id}/deactivate", response_model=UnitOut, dependencies=[Depends(require_role("admin"))])
def deactivate_unit(unit_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Soft-deactivation — the unit row and every booking/payment/invoice
    history against it are untouched. A deactivated unit is simply
    excluded from new-booking pickers/availability (see
    bookings/router.py) and shown with an inactive badge in the UI.
    """
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if not unit.is_active:
        raise HTTPException(409, "Unit is already deactivated")
    unit.is_active = False
    record_audit_log(db, user.id, "unit.deactivate", entity_type="unit", entity_id=str(unit.id), details=f"Deactivated: {unit.name}")
    db.commit()
    db.refresh(unit)
    return unit


@router.patch("/units/{unit_id}/reactivate", response_model=UnitOut, dependencies=[Depends(require_role("admin"))])
def reactivate_unit(unit_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if unit.is_active:
        raise HTTPException(409, "Unit is already active")
    unit.is_active = True
    record_audit_log(db, user.id, "unit.reactivate", entity_type="unit", entity_id=str(unit.id), details=f"Reactivated: {unit.name}")
    db.commit()
    db.refresh(unit)
    return unit


UNIT_PHOTO_STORAGE_DIR = os.environ.get("UNIT_PHOTO_STORAGE_DIR", "/tmp/sri_apartment_unit_photos")
os.makedirs(UNIT_PHOTO_STORAGE_DIR, exist_ok=True)


@router.post("/units/{unit_id}/photos", response_model=UnitPhotoOut, status_code=201, dependencies=[Depends(require_role("admin", "staff"))])
async def upload_unit_photo(
    unit_id: uuid.UUID, caption: str | None = None, file: UploadFile = File(...),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")

    contents = await read_and_validate_upload(file, allowed_content_types=IMAGE_CONTENT_TYPES)
    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{unit_id}_{uuid.uuid4().hex[:8]}{ext}"
    stored_path = os.path.join(UNIT_PHOTO_STORAGE_DIR, stored_name)
    with open(stored_path, "wb") as f:
        f.write(contents)

    record = UnitPhoto(
        unit_id=unit_id, filename=file.filename or stored_name,
        content_type=file.content_type or "application/octet-stream", file_path=stored_path,
        caption=caption, uploaded_by=user.id,
    )
    db.add(record)
    db.flush()
    record_audit_log(db, user.id, "unit.photo_upload", entity_type="unit", entity_id=str(unit_id), details=record.filename)
    db.commit()
    db.refresh(record)
    return record


@router.get("/units/{unit_id}/photos", response_model=list[UnitPhotoOut], dependencies=[Depends(require_role("admin", "staff"))])
def list_unit_photos(unit_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(UnitPhoto).where(UnitPhoto.unit_id == unit_id)).all()


@router.get("/units/{unit_id}/photos/{photo_id}/download", dependencies=[Depends(require_role("admin", "staff"))])
def download_unit_photo(unit_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db)):
    record = db.get(UnitPhoto, photo_id)
    if record is None or record.unit_id != unit_id:
        raise HTTPException(404, "Photo not found")
    if not os.path.exists(record.file_path):
        raise HTTPException(404, "The stored file is missing from disk")
    return FileResponse(record.file_path, media_type=record.content_type, filename=record.filename)


@router.delete("/units/{unit_id}/photos/{photo_id}", status_code=204, dependencies=[Depends(require_role("admin", "staff"))])
def delete_unit_photo(unit_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    record = db.get(UnitPhoto, photo_id)
    if record is None or record.unit_id != unit_id:
        raise HTTPException(404, "Photo not found")
    record_audit_log(db, user.id, "unit.photo_delete", entity_type="unit", entity_id=str(unit_id), details=f"Deleted: {record.filename}")
    if os.path.exists(record.file_path):
        os.remove(record.file_path)
    db.delete(record)
    db.commit()


# --- Seasonal rates ---

@router.post(
    "/units/{unit_id}/seasonal-rates",
    response_model=SeasonalRateOut,
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
def create_seasonal_rate(unit_id: uuid.UUID, payload: SeasonalRateCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    rate = SeasonalRate(unit_id=unit_id, **payload.model_dump())
    db.add(rate)
    db.flush()
    record_audit_log(db, user.id, "seasonal_rate.create", entity_type="seasonal_rate", entity_id=str(rate.id), details=f"{payload.name}: Rs.{payload.rate_per_night}/night, {payload.start_date} to {payload.end_date}, unit {unit_id}")
    db.commit()
    db.refresh(rate)
    return rate


@router.get(
    "/units/{unit_id}/seasonal-rates",
    response_model=list[SeasonalRateOut],
    dependencies=[Depends(require_role("admin", "staff"))],
)
def list_seasonal_rates(unit_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(select(SeasonalRate).where(SeasonalRate.unit_id == unit_id).order_by(SeasonalRate.start_date)).all()


@router.delete(
    "/units/{unit_id}/seasonal-rates/{rate_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
def delete_seasonal_rate(unit_id: uuid.UUID, rate_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rate = db.get(SeasonalRate, rate_id)
    if rate is None or rate.unit_id != unit_id:
        raise HTTPException(404, "Seasonal rate not found")
    record_audit_log(db, user.id, "seasonal_rate.delete", entity_type="seasonal_rate", entity_id=str(rate_id), details=f"{rate.name}, unit {unit_id}")
    db.delete(rate)
    db.commit()


# --- Long-stay discount tiers ---

@router.post(
    "/units/{unit_id}/long-stay-discounts",
    response_model=LongStayDiscountTierOut,
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
def create_discount_tier(unit_id: uuid.UUID, payload: LongStayDiscountTierCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    tier = LongStayDiscountTier(unit_id=unit_id, **payload.model_dump())
    db.add(tier)
    db.flush()
    record_audit_log(db, user.id, "discount_tier.create", entity_type="discount_tier", entity_id=str(tier.id), details=f"{payload.min_nights}+ nights: {payload.discount_percent}% off, unit {unit_id}")
    db.commit()
    db.refresh(tier)
    return tier


@router.get(
    "/units/{unit_id}/long-stay-discounts",
    response_model=list[LongStayDiscountTierOut],
    dependencies=[Depends(require_role("admin", "staff"))],
)
def list_discount_tiers(unit_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(
        select(LongStayDiscountTier).where(LongStayDiscountTier.unit_id == unit_id).order_by(LongStayDiscountTier.min_nights)
    ).all()


@router.delete(
    "/units/{unit_id}/long-stay-discounts/{tier_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
def delete_discount_tier(unit_id: uuid.UUID, tier_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    tier = db.get(LongStayDiscountTier, tier_id)
    if tier is None or tier.unit_id != unit_id:
        raise HTTPException(404, "Discount tier not found")
    record_audit_log(db, user.id, "discount_tier.delete", entity_type="discount_tier", entity_id=str(tier_id), details=f"{tier.min_nights}+ nights, unit {unit_id}")
    db.delete(tier)
    db.commit()


# --- Price quote (the actual dynamic pricing calculation) ---

def get_unit_price_quote(db: Session, unit: Unit, check_in: date, check_out: date):
    """Shared helper — used by both the /price-quote endpoint (for live
    frontend previews) and inquiry creation (for the authoritative price
    used on the actual quotation)."""
    seasonal = [
        {"id": s.id, "name": s.name, "start_date": s.start_date, "end_date": s.end_date, "rate_per_night": float(s.rate_per_night), "created_at": s.created_at}
        for s in db.scalars(select(SeasonalRate).where(SeasonalRate.unit_id == unit.id)).all()
    ]
    tiers = [
        {"min_nights": t.min_nights, "discount_percent": float(t.discount_percent)}
        for t in db.scalars(select(LongStayDiscountTier).where(LongStayDiscountTier.unit_id == unit.id)).all()
    ]
    return calculate_price(
        check_in=check_in,
        check_out=check_out,
        base_rate=float(unit.base_rate),
        weekend_rate=float(unit.weekend_rate) if unit.weekend_rate is not None else None,
        seasonal_rates=seasonal,
        discount_tiers=tiers,
    )


@router.get(
    "/units/{unit_id}/price-quote",
    response_model=PriceQuoteOut,
    dependencies=[Depends(require_role("admin", "staff"))],
)
def price_quote(unit_id: uuid.UUID, check_in: date = Query(...), check_out: date = Query(...), db: Session = Depends(get_db)):
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise HTTPException(404, "Unit not found")
    if check_out <= check_in:
        raise HTTPException(400, "check_out must be after check_in")
    quote = get_unit_price_quote(db, unit, check_in, check_out)
    return PriceQuoteOut(
        check_in=quote.check_in,
        check_out=quote.check_out,
        nights=quote.nights,
        daily_breakdown=[{"the_date": d.the_date, "rate": d.rate, "source": d.source, "seasonal_name": d.seasonal_name} for d in quote.daily_breakdown],
        subtotal_before_discount=quote.subtotal_before_discount,
        long_stay_discount_percent=quote.long_stay_discount_percent,
        long_stay_discount_amount=quote.long_stay_discount_amount,
        subtotal_after_discount=quote.subtotal_after_discount,
        average_nightly_rate=quote.average_nightly_rate,
    )
