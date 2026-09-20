import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.audit.schemas import record_audit_log
from app.modules.notifications.service import trigger_low_stock_alert
from app.modules.inventory.models import ITEM_CATEGORIES, InventoryItem, StockMovement, Supplier
from app.modules.inventory.schemas import (
    InventoryItemCreate,
    InventoryItemOut,
    InventoryItemUpdate,
    InventoryReport,
    StockMovementCreate,
    StockMovementOut,
    SupplierCreate,
    SupplierOut,
)
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


# --- Suppliers ---

@router.post("/suppliers", response_model=SupplierOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def create_supplier(payload: SupplierCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    supplier = Supplier(**payload.model_dump())
    db.add(supplier)
    db.flush()
    record_audit_log(db, user.id, "inventory.supplier.create", entity_type="supplier", entity_id=str(supplier.id), details=payload.name)
    db.commit()
    db.refresh(supplier)
    return supplier


@router.get("/suppliers", response_model=list[SupplierOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_suppliers(db: Session = Depends(get_db)):
    return db.scalars(select(Supplier)).all()


# --- Items ---

@router.post("/items", response_model=InventoryItemOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def create_item(payload: InventoryItemCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.category not in ITEM_CATEGORIES:
        raise HTTPException(400, f"Invalid category '{payload.category}'. Must be one of {ITEM_CATEGORIES}")
    if payload.sku:
        existing = db.scalar(select(InventoryItem).where(InventoryItem.sku == payload.sku))
        if existing:
            raise HTTPException(400, f"SKU '{payload.sku}' is already used by another item")
    if payload.barcode:
        existing = db.scalar(select(InventoryItem).where(InventoryItem.barcode == payload.barcode))
        if existing:
            raise HTTPException(400, f"Barcode '{payload.barcode}' is already used by another item")
    item = InventoryItem(**payload.model_dump())
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        # Belt-and-suspenders for the SKU/barcode pre-checks above: those
        # protect the common case, but a second request racing in between
        # the check and this flush would otherwise hit a raw, unhandled
        # uniqueness violation here instead of the same clean message.
        db.rollback()
        raise HTTPException(400, "SKU or barcode is already used by another item")
    record_audit_log(db, user.id, "inventory.item.create", entity_type="inventory_item", entity_id=str(item.id), details=payload.name)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=InventoryItemOut, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def update_item(item_id: uuid.UUID, payload: InventoryItemUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No fields to update were provided")
    if "category" in changes and changes["category"] not in ITEM_CATEGORIES:
        raise HTTPException(400, f"Invalid category '{changes['category']}'. Must be one of {ITEM_CATEGORIES}")
    if changes.get("sku"):
        existing = db.scalar(select(InventoryItem).where(InventoryItem.sku == changes["sku"], InventoryItem.id != item_id))
        if existing:
            raise HTTPException(400, f"SKU '{changes['sku']}' is already used by another item")
    if changes.get("barcode"):
        existing = db.scalar(select(InventoryItem).where(InventoryItem.barcode == changes["barcode"], InventoryItem.id != item_id))
        if existing:
            raise HTTPException(400, f"Barcode '{changes['barcode']}' is already used by another item")
    for field, value in changes.items():
        setattr(item, field, value)
    try:
        db.flush()
    except IntegrityError:
        # Same race-window backstop as create_item — see comment there.
        db.rollback()
        raise HTTPException(400, "SKU or barcode is already used by another item")
    record_audit_log(db, user.id, "inventory.item.update", entity_type="inventory_item", entity_id=str(item.id), details=f"Updated: {', '.join(changes.keys())}")
    db.commit()
    db.refresh(item)
    return item


@router.get("/items", response_model=list[InventoryItemOut], dependencies=[Depends(require_role("admin", "staff", "manager"))])
def list_items(category: str | None = None, low_stock_only: bool = False, db: Session = Depends(get_db)):
    query = select(InventoryItem)
    if category:
        query = query.where(InventoryItem.category == category)
    items = db.scalars(query).all()
    if low_stock_only:
        items = [i for i in items if i.is_low_stock]
    return items


# --- Stock movements ---

@router.post(
    "/stock-in", response_model=StockMovementOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))]
)
def stock_in(payload: StockMovementCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # with_for_update locks the row for the rest of this transaction — the
    # same idiom already used for InvoiceCounter — so a concurrent stock
    # movement on the same item can't read a stale current_stock and
    # silently lose an update.
    item = db.get(InventoryItem, payload.item_id, with_for_update=True)
    if item is None:
        raise HTTPException(404, "Item not found")

    movement = StockMovement(
        item_id=payload.item_id,
        movement_type="stock_in",
        quantity=payload.quantity,
        reason=payload.reason or "purchase",
        supplier_id=payload.supplier_id,
        unit_cost=payload.unit_cost,
        unit_id=payload.unit_id,
        housekeeping_task_id=payload.housekeeping_task_id,
        created_by=user.id,
    )
    item.current_stock += payload.quantity
    if payload.unit_cost is not None:
        item.unit_cost = payload.unit_cost  # most recent purchase cost
    db.add(movement)
    db.flush()
    record_audit_log(db, user.id, "inventory.stock_in", entity_type="stock_movement", entity_id=str(movement.id), details=f"+{payload.quantity} {item.name}")

    # Finance integration: a stock-in with both a supplier and a unit cost
    # is a real purchase, not just an internal stock adjustment — it needs
    # to actually appear in the Purchase Register, the same way a
    # maintenance ticket's cost needs to appear as a real Expense. Without
    # this, the Purchase Register never reflects a business's actual
    # supply buying, no matter how much inventory gets logged.
    if payload.supplier_id and payload.unit_cost:
        from app.modules.finance.models import PurchaseRecord
        supplier = db.get(Supplier, payload.supplier_id)
        purchase = PurchaseRecord(
            purchase_date=movement.created_at.date(),
            item_description=f"{item.name} (inventory stock-in)",
            amount=round(float(payload.unit_cost) * payload.quantity, 2),
            supplier_name=supplier.name if supplier else None,
            category=item.category,
            quantity=payload.quantity,
            unit_price=payload.unit_cost,
            unit_id=payload.unit_id,
            created_by=user.id,
        )
        db.add(purchase)
        db.flush()
        movement.linked_purchase_record_id = purchase.id
        record_audit_log(db, user.id, "finance.purchase.create", entity_type="purchase_record", entity_id=str(purchase.id), details=f"From inventory stock-in of {item.name}: Rs.{purchase.amount}")

    db.commit()
    db.refresh(movement)
    return movement


@router.post(
    "/stock-out", response_model=StockMovementOut, status_code=201, dependencies=[Depends(require_role("admin", "staff", "manager"))]
)
def stock_out(payload: StockMovementCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Same row-lock rationale as stock_in — see comment there.
    item = db.get(InventoryItem, payload.item_id, with_for_update=True)
    if item is None:
        raise HTTPException(404, "Item not found")
    if payload.quantity > item.current_stock:
        raise HTTPException(400, f"Cannot remove {payload.quantity} units — only {item.current_stock} in stock")

    was_low_stock_before = item.is_low_stock
    movement = StockMovement(
        item_id=payload.item_id,
        movement_type="stock_out",
        quantity=payload.quantity,
        reason=payload.reason or "usage",
        unit_id=payload.unit_id,
        housekeeping_task_id=payload.housekeeping_task_id,
        maintenance_ticket_id=payload.maintenance_ticket_id,
        created_by=user.id,
    )
    item.current_stock -= payload.quantity
    db.add(movement)
    db.flush()
    record_audit_log(db, user.id, "inventory.stock_out", entity_type="stock_movement", entity_id=str(movement.id), details=f"-{payload.quantity} {item.name}" + (f" (housekeeping task {payload.housekeeping_task_id})" if payload.housekeeping_task_id else "") + (f" (maintenance ticket {payload.maintenance_ticket_id})" if payload.maintenance_ticket_id else ""))

    # Notification fires only at the moment stock CROSSES into low —
    # not on every subsequent stock-out while it's already low — so this
    # doesn't spam one alert per stock-out once an item is already flagged.
    if item.is_low_stock and not was_low_stock_before:
        trigger_low_stock_alert(db, item)

    db.commit()
    db.refresh(movement)
    return movement


@router.get(
    "/items/{item_id}/movements",
    response_model=list[StockMovementOut],
    dependencies=[Depends(require_role("admin", "staff", "manager"))],
)
def list_movements(
    item_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(StockMovement).where(StockMovement.item_id == item_id)
        .order_by(StockMovement.created_at.desc()).offset(offset).limit(limit)
    ).all()


# --- Reports ---

@router.get("/reports/summary", response_model=InventoryReport, dependencies=[Depends(require_role("admin", "staff", "manager"))])
def inventory_report(db: Session = Depends(get_db)):
    items = db.scalars(select(InventoryItem)).all()
    by_category: dict[str, int] = defaultdict(int)
    total_value = 0.0
    low_stock = 0
    for item in items:
        by_category[item.category] += item.current_stock
        if item.unit_cost:
            total_value += float(item.unit_cost) * item.current_stock
        if item.is_low_stock:
            low_stock += 1

    return InventoryReport(
        total_items=len(items),
        low_stock_count=low_stock,
        total_stock_value=round(total_value, 2),
        by_category=dict(by_category),
    )
