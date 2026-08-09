import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class SupplierCreate(BaseModel):
    name: str
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    notes: str | None = None


class SupplierOut(SupplierCreate):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class InventoryItemCreate(BaseModel):
    name: str
    category: str = "other"
    unit_of_measure: str = "pcs"
    minimum_stock: int = 0
    barcode: str | None = None
    qr_code_value: str | None = None
    unit_cost: float | None = None
    notes: str | None = None
    brand: str | None = None
    expiry_date: date | None = None
    sku: str | None = None


class InventoryItemOut(InventoryItemCreate):
    id: uuid.UUID
    current_stock: int
    is_low_stock: bool
    created_at: datetime

    class Config:
        from_attributes = True


class InventoryItemUpdate(BaseModel):
    """All fields optional — was previously no way to edit an item at all
    after creation (e.g. to correct a minimum_stock threshold or add a
    barcode later)."""
    name: str | None = None
    category: str | None = None
    unit_of_measure: str | None = None
    minimum_stock: int | None = None
    barcode: str | None = None
    qr_code_value: str | None = None
    unit_cost: float | None = None
    notes: str | None = None
    brand: str | None = None
    expiry_date: date | None = None
    sku: str | None = None


class StockMovementCreate(BaseModel):
    item_id: uuid.UUID
    quantity: int = Field(gt=0)
    reason: str | None = None
    supplier_id: uuid.UUID | None = None
    unit_cost: float | None = None
    unit_id: uuid.UUID | None = None
    housekeeping_task_id: uuid.UUID | None = None
    maintenance_ticket_id: uuid.UUID | None = None


class StockMovementOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    movement_type: str
    quantity: int
    reason: str | None
    supplier_id: uuid.UUID | None
    unit_cost: float | None
    unit_id: uuid.UUID | None
    housekeeping_task_id: uuid.UUID | None
    maintenance_ticket_id: uuid.UUID | None
    linked_purchase_record_id: uuid.UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class InventoryReport(BaseModel):
    total_items: int
    low_stock_count: int
    total_stock_value: float
    by_category: dict[str, int]
