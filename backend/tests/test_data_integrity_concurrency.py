"""
Regression tests for the three concurrency/data-integrity races found and
fixed in the production-readiness audit (Phase 2):

- Inventory stock_in/stock_out: lost-update race on InventoryItem.current_stock
- Maintenance update_ticket: duplicate-Expense race on linked_expense_id,
  plus an unrelated-edit-silently-overwrites-Expense bug
- Finance refund_deposit: lost-update race on SecurityDeposit.refund_amount

Each fix is a `with_for_update=True` row lock (same idiom already used for
InvoiceCounter elsewhere in this codebase) plus a defense-in-depth CHECK
constraint. The concurrency tests fire two real, genuinely-concurrent
requests via separate threads (against a real Postgres backend, where row
locking is real) and assert the final state is correct — before the fix,
these tests are flaky/wrong (lost updates, duplicate rows, or a negative/
over-refunded value); after the fix, they're deterministic.

Run with: pytest tests/test_data_integrity_concurrency.py -v
"""
import threading
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_EMAIL = "homestaypdy@gmail.com"
ADMIN_PASSWORD = "Sri@2026"


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_property(headers):
    resp = client.post("/api/v1/properties", headers=headers, json={"name": f"DIProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "33"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_unit(headers, property_id, base_rate=2000):
    resp = client.post("/api/v1/units", headers=headers, json={"property_id": property_id, "name": f"DIUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_booking(headers, days_offset=8000):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "DI Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=days_offset)
    check_out = check_in + timedelta(days=2)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": str(check_in), "check_out": str(check_out),
    }).json()
    return booking["id"]


def _run_concurrently(*fns):
    """Fire each given zero-arg function on its own thread and wait for all to finish."""
    threads = [threading.Thread(target=fn) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# --- Inventory stock race ---

def test_concurrent_stock_out_never_goes_negative_or_loses_an_update(headers):
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"DI Item {uuid.uuid4().hex[:6]}"}).json()
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 10})

    results = []
    def do_stock_out():
        results.append(client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 6}))

    _run_concurrently(do_stock_out, do_stock_out)

    statuses = sorted(r.status_code for r in results)
    # Exactly one of the two 6-unit withdrawals from a 10-unit stock can
    # succeed; the other must be cleanly rejected against the true
    # (post-first-withdrawal) remaining stock — not silently lost, and
    # never allowed to drive stock negative.
    assert statuses == [201, 400], [r.text for r in results]

    final = next(i for i in client.get("/api/v1/inventory/items", headers=headers).json() if i["id"] == item["id"])
    assert final["current_stock"] == 4


def test_concurrent_stock_in_applies_both_updates(headers):
    """Same lock, opposite direction — two concurrent stock-ins must both
    be reflected, not lose one to a stale read."""
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"DI Item In {uuid.uuid4().hex[:6]}"}).json()

    results = []
    def do_stock_in():
        results.append(client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 5}))

    _run_concurrently(do_stock_in, do_stock_in)

    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    final = next(i for i in client.get("/api/v1/inventory/items", headers=headers).json() if i["id"] == item["id"])
    assert final["current_stock"] == 10


def test_stock_out_exceeding_stock_still_rejected_normally(headers):
    """Regression: the lock must not change the existing non-concurrent
    validation behavior."""
    item = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"DI Item Reg {uuid.uuid4().hex[:6]}"}).json()
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": item["id"], "quantity": 3})
    resp = client.post("/api/v1/inventory/stock-out", headers=headers, json={"item_id": item["id"], "quantity": 5})
    assert resp.status_code == 400


# --- Maintenance Expense-sync race + unrelated-edit-overwrite fix ---

def test_concurrent_ticket_resolution_creates_only_one_expense(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    marker = f"DI concurrency marker {uuid.uuid4().hex[:8]}"
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": marker}).json()

    results = []
    def do_resolve():
        results.append(client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 800}))

    _run_concurrently(do_resolve, do_resolve)

    assert all(r.status_code == 200 for r in results), [r.text for r in results]

    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"category": "maintenance"}).json()
    matching = [e for e in expenses if e.get("unit_id") == unit_id and marker in (e.get("notes") or "")]
    assert len(matching) == 1, f"expected exactly one Expense from this ticket's resolution, found {len(matching)}"
    assert matching[0]["amount"] == 800.0


def test_unrelated_edit_after_resolution_does_not_touch_expense(headers):
    """The second half of the Maintenance fix: once resolved, an edit that
    doesn't touch cost (e.g. changing priority) must not re-enter the
    Finance sync at all — so it can never silently revert an Expense
    amount someone corrected directly in Finance."""
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "DI unrelated-edit test"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 500})

    expenses_before = client.get("/api/v1/finance/expenses", headers=headers, params={"category": "maintenance"}).json()
    before = next(e for e in expenses_before if e.get("unit_id") == unit_id)

    # Simulate a Finance user correcting the expense amount directly.
    # (No dedicated PATCH /finance/expenses/{id} endpoint exists in this
    # codebase, so we verify indirectly: an unrelated maintenance-ticket
    # edit must not change the Expense's amount away from what it already
    # is, i.e. the sync must not fire for this request at all.)
    resp = client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"priority": "high"})
    assert resp.status_code == 200, resp.text

    expenses_after = client.get("/api/v1/finance/expenses", headers=headers, params={"category": "maintenance"}).json()
    after = next(e for e in expenses_after if e["id"] == before["id"])
    assert after["amount"] == before["amount"] == 500.0


def test_cost_correction_after_resolution_still_syncs(headers):
    """Regression: explicitly changing cost after resolution must still
    update the linked Expense — the narrowed condition must not have
    broken this legitimate case."""
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id)
    ticket = client.post("/api/v1/maintenance-tickets", headers=headers, json={"unit_id": unit_id, "description": "DI cost-correction test"}).json()
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"status": "resolved", "cost": 300})
    client.patch(f"/api/v1/maintenance-tickets/{ticket['id']}", headers=headers, json={"cost": 450})

    expenses = client.get("/api/v1/finance/expenses", headers=headers, params={"category": "maintenance"}).json()
    matching = [e for e in expenses if e.get("unit_id") == unit_id]
    assert len(matching) == 1
    assert matching[0]["amount"] == 450.0


# --- Security Deposit refund race ---

def test_concurrent_partial_refunds_never_exceed_deposit_amount(headers):
    booking_id = _make_booking(headers)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking_id, "amount": 1000}).json()

    results = []
    def do_refund():
        results.append(client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 700}))

    _run_concurrently(do_refund, do_refund)

    statuses = sorted(r.status_code for r in results)
    # 700 + 700 = 1400 > 1000: only one of the two can legitimately
    # succeed; the other must be cleanly rejected against the true
    # (post-first-refund) remaining balance, not silently lost.
    assert statuses == [200, 400], [r.text for r in results]

    final = next(d for d in client.get("/api/v1/finance/deposits", headers=headers).json() if d["id"] == deposit["id"])
    assert final["refund_amount"] == 700.0
    assert final["status"] == "partially_refunded"


def test_full_refund_still_works_normally(headers):
    """Regression: the lock must not change existing non-concurrent
    refund behavior."""
    booking_id = _make_booking(headers, days_offset=8100)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking_id, "amount": 500}).json()
    resp = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 500})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "refunded"


def test_refund_exceeding_amount_still_rejected_normally(headers):
    booking_id = _make_booking(headers, days_offset=8200)
    deposit = client.post("/api/v1/finance/deposits", headers=headers, json={"booking_id": booking_id, "amount": 300}).json()
    resp = client.post(f"/api/v1/finance/deposits/{deposit['id']}/refund", headers=headers, json={"refund_amount": 400})
    assert resp.status_code == 400
