import uuid
from datetime import datetime

from pydantic import BaseModel


class HousekeepingTaskCreate(BaseModel):
    unit_id: uuid.UUID
    assigned_to: uuid.UUID | None = None
    task_type: str = "routine"  # checkout_clean/routine/deep_clean/inspection
    notes: str | None = None


class HousekeepingTaskUpdate(BaseModel):
    status: str | None = None  # pending/in_progress/completed
    assigned_to: uuid.UUID | None = None
    notes: str | None = None


class HousekeepingTaskOut(BaseModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    assigned_to: uuid.UUID | None
    task_type: str
    status: str
    notes: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    class Config:
        from_attributes = True


class HousekeepingPhotoOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    filename: str
    content_type: str
    caption: str | None
    uploaded_at: datetime

    class Config:
        from_attributes = True
