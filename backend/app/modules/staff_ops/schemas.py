import uuid
from datetime import date, datetime

from pydantic import BaseModel


class StaffDashboardOut(BaseModel):
    date: date
    check_ins_today: int
    check_outs_today: int
    occupied_units: int
    vacant_units: int
    units_needing_cleaning: int
    open_maintenance_tickets: int
    open_guest_requests: int
    pending_staff_tasks: int


class CheckInLine(BaseModel):
    booking_id: uuid.UUID
    guest_name: str
    unit_id: uuid.UUID
    unit_name: str
    arrival_time: str | None
    adults: int
    children: int
    status: str
    net_paid: float
    # Identity Verification (section 12/18 — display-only at check-in, no
    # blocking) — the Principal Guest's existing Guest.identity_status,
    # reused from the guest lookup this function already does, not a new
    # query. Never implies Account Verification.
    guest_identity_status: str


class CheckOutLine(BaseModel):
    booking_id: uuid.UUID
    guest_name: str
    unit_id: uuid.UUID
    unit_name: str
    departure_time: str | None
    status: str
    housekeeping_status: str


class StaffTaskOut(BaseModel):
    """
    A normalized view over three existing tables (housekeeping_tasks,
    maintenance_tickets, guest_requests) — not a new table. `category`
    tells the caller (and PATCH /staff/tasks/{id}?category=...) which of
    the three underlying rows this is, so the frontend can show one list
    without needing to know which table each row actually lives in.
    """
    id: uuid.UUID
    category: str  # "housekeeping" | "maintenance" | "guest_request"
    unit_id: uuid.UUID | None
    unit_name: str | None
    booking_id: uuid.UUID | None  # only guest_request tasks are booking-linked — null for housekeeping/maintenance
    label: str
    priority: str | None  # housekeeping tasks have no priority concept — null for those
    status: str
    assigned_to: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None  # only meaningful for housekeeping — null for maintenance/guest_request
    completed_at: datetime | None  # housekeeping_tasks.completed_at / maintenance_tickets.resolved_at / guest_requests.resolved_at
    notes: str | None


class StaffTaskUpdate(BaseModel):
    status: str | None = None
    assigned_to: uuid.UUID | None = None
    notes: str | None = None
