import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationLogOut(BaseModel):
    id: uuid.UUID
    event_type: str
    channel: str
    recipient: str
    booking_id: uuid.UUID | None
    inquiry_id: uuid.UUID | None
    guest_request_id: uuid.UUID | None
    maintenance_ticket_id: uuid.UUID | None
    housekeeping_task_id: uuid.UUID | None
    inventory_item_id: uuid.UUID | None
    leave_request_id: uuid.UUID | None
    payroll_id: uuid.UUID | None
    user_id: uuid.UUID | None
    read_at: datetime | None
    success: bool
    error_message: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class ReminderScanResult(BaseModel):
    checkin_reminder: int
    checkout_reminder: int
    review_request: int
    payment_reminder: int
