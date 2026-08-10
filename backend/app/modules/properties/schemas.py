import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


class PropertyCreate(BaseModel):
    name: str
    address: str = ""
    gst_number: str | None = None
    state_code: str = Field(min_length=2, max_length=2)
    property_code: str | None = None
    property_type: str | None = None
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_email: str | None = None
    google_maps_url: str | None = None


class PropertyOut(PropertyCreate):
    id: uuid.UUID
    is_active: bool = True

    class Config:
        from_attributes = True


class PropertyUpdate(BaseModel):
    """All fields optional — there was previously no way to edit a
    property at all after creation (fix a typo in the address, update
    owner contact details, add a GST number later)."""
    name: str | None = None
    address: str | None = None
    gst_number: str | None = None
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
    property_code: str | None = None
    property_type: str | None = None
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_email: str | None = None
    google_maps_url: str | None = None


class SeasonalRateCreate(BaseModel):
    name: str
    start_date: date
    end_date: date
    rate_per_night: float = Field(gt=0)

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v, info):
        start = info.data.get("start_date")
        if start and v < start:
            raise ValueError("end_date must be on or after start_date")
        return v


class SeasonalRateOut(SeasonalRateCreate):
    id: uuid.UUID
    unit_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class LongStayDiscountTierCreate(BaseModel):
    min_nights: int = Field(gt=0)
    discount_percent: float = Field(gt=0, le=100)


class LongStayDiscountTierOut(LongStayDiscountTierCreate):
    id: uuid.UUID
    unit_id: uuid.UUID

    class Config:
        from_attributes = True


class DayRateOut(BaseModel):
    the_date: date
    rate: float
    source: str
    seasonal_name: str | None = None


class PriceQuoteOut(BaseModel):
    check_in: date
    check_out: date
    nights: int
    daily_breakdown: list[DayRateOut]
    subtotal_before_discount: float
    long_stay_discount_percent: float
    long_stay_discount_amount: float
    subtotal_after_discount: float
    average_nightly_rate: float


class PropertyFileOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    file_type: str
    filename: str
    content_type: str
    caption: str | None
    uploaded_at: datetime

    class Config:
        from_attributes = True


class UnitPhotoOut(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    filename: str
    content_type: str
    caption: str | None
    uploaded_at: datetime

    class Config:
        from_attributes = True
