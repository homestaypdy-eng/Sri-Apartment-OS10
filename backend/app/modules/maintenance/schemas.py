import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MaintenanceTicketCreate(BaseModel):
    unit_id: uuid.UUID
    description: str
    priority: str = "medium"  # low/medium/high/urgent
    category: str | None = None  # ac/electrical/plumbing/water/appliance/furniture/internet/other
    assigned_to: uuid.UUID | None = None


class MaintenanceTicketUpdate(BaseModel):
    status: str | None = None  # open/in_progress/resolved/closed
    assigned_to: uuid.UUID | None = None
    priority: str | None = None
    category: str | None = None
    cost: float | None = Field(None, ge=0)


class MaintenanceTicketOut(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    reported_by: uuid.UUID | None
    assigned_to: uuid.UUID | None
    priority: str
    status: str
    category: str | None
    description: str
    cost: float | None
    created_at: datetime
    resolved_at: datetime | None

    class Config:
        from_attributes = True


class MaintenancePhotoOut(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    photo_type: str
    filename: str
    content_type: str
    uploaded_at: datetime

    class Config:
        from_attributes = True
