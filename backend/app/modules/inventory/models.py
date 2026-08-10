import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

ITEM_CATEGORIES = (
    "bedsheets", "pillow_covers", "towels", "blankets", "toiletries",
    "cleaning_supplies", "kitchen_items", "appliances", "furniture", "other",
)
MOVEMENT_TYPES = ("stock_in", "stock_out")


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    contact_person: Mapped[str | None] = mapped_column(String(150), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint(f"category IN {ITEM_CATEGORIES}", name="ck_inventory_category"),
        # Defense-in-depth backstop for the row-locked stock_in/stock_out
        # handlers in the router — belt-and-suspenders against current_stock
        # ever going negative, not the primary guard (that's the lock).
        CheckConstraint("current_stock >= 0", name="ck_inventory_stock_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(30), default="other")
    unit_of_measure: Mapped[str] = mapped_column(String(20), default="pcs")  # pcs/kg/litre/box...
    current_stock: Mapped[int] = mapped_column(default=0)
    minimum_stock: Mapped[int] = mapped_column(default=0)
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    qr_code_value: Mapped[str | None] = mapped_column(String(200), nullable=True)  # arbitrary payload encoded into a QR by the frontend
    unit_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # A human-assigned internal reference code (e.g. "TWL-WHT-S"), distinct
    # from barcode (a scannable manufacturer/retail code) — a business can
    # have one, the other, both, or neither; this is the business's own
    # internal stock-keeping identifier, not something derived from barcode.
    sku: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    @property
    def is_low_stock(self) -> bool:
        return self.current_stock <= self.minimum_stock


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (CheckConstraint(f"movement_type IN {MOVEMENT_TYPES}", name="ck_movement_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "purchase", "housekeeping use", "damaged"
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)  # relevant for stock_in / purchases
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units.id"), nullable=True)
    housekeeping_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("housekeeping_tasks.id"), nullable=True)
    # Maintenance integration: a repair that consumes parts/materials from
    # stock (e.g. "used 2 light bulbs fixing the ceiling fan") links here,
    # the same way housekeeping_task_id links a cleaning's consumption.
    maintenance_ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("maintenance_tickets.id"), nullable=True)
    # Finance integration: a stock-in with a supplier is a real purchase —
    # this links to the PurchaseRecord auto-created for it, the same
    # pattern used for maintenance-cost-to-Expense in the Maintenance
    # module, so the Purchase Register actually reflects inventory buying,
    # not just a disconnected stock count.
    linked_purchase_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_records.id"), nullable=True)

    item = relationship("InventoryItem")
    supplier = relationship("Supplier")
