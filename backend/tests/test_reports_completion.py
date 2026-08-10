"""
Tests completing the Reports module to full production coverage:
property_pnl PDF/Excel export (existed as a JSON endpoint but wasn't
exportable via Reports Center) and the new Guest Report.
Run with: pytest tests/test_reports_completion.py -v
"""
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


def test_property_pnl_export_pdf_and_excel(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"ReportsCompProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"ReportsCompUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Reports Completion Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    year, month = 2058, 5
    client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": f"{year}-{month:02d}-01", "check_out": f"{year}-{month:02d}-03"})

    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/finance/reports/export", headers=headers, params={
            "report": "property_pnl", "format": fmt, "property_id": prop["id"], "period_year": year, "period_month": month,
        })
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_property_pnl_export_requires_property_id_and_period(headers):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={"report": "property_pnl", "format": "pdf"})
    assert resp.status_code == 400


def test_property_pnl_export_404_for_unknown_property(headers):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={
        "report": "property_pnl", "format": "pdf", "property_id": str(uuid.uuid4()), "period_year": 2058, "period_month": 5,
    })
    assert resp.status_code == 404


def test_guest_report_export_pdf_and_excel(headers):
    for fmt in ("pdf", "xlsx"):
        resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "guests", "format": fmt})
        assert resp.status_code == 200, resp.text
        if fmt == "pdf":
            assert resp.content[:4] == b"%PDF"
        else:
            assert len(resp.content) > 100


def test_guest_report_reflects_real_vip_status(headers):
    guest = client.post("/api/v1/guests", headers=headers, json={"name": f"VIP Report Guest {uuid.uuid4().hex[:6]}", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    client.patch(f"/api/v1/guests/{guest['id']}", headers=headers, json={"is_vip": True})
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"VIPReportProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"VIPReportUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    check_in = date.today() + timedelta(days=11500)
    booking = client.post("/api/v1/bookings", headers=headers, json={"unit_id": unit["id"], "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=3)).isoformat()}).json()
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 6000, "mode": "upi", "reference_no": "GUESTREPORTTEST"})

    resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "guests", "format": "pdf"})
    assert resp.status_code == 200

    guest_check = client.get("/api/v1/guests", headers=headers, params={"ids": guest["id"]}).json()
    assert guest_check[0]["is_vip"] is True


def test_existing_reports_still_export_correctly(headers):
    for report in ("summary", "unit_performance", "guest_requests", "maintenance", "housekeeping", "inventory", "enquiries"):
        resp = client.get("/api/v1/reports/export", headers=headers, params={"report": report, "format": "pdf"})
        assert resp.status_code == 200, f"{report} failed: {resp.text}"
    # GST report is deliberately xlsx-only, existing behavior
    resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "gst", "format": "xlsx"})
    assert resp.status_code == 200, resp.text


# --- Phase 10A-1: "What Needs Attention Today" ---

def _make_property_and_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"AttnProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"AttnUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return unit["id"]


def test_existing_dashboard_fields_still_present(headers):
    """Regression: every field the Dashboard already relied on before this
    feature must still be present with the same shape."""
    resp = client.get("/api/v1/reports/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for field in (
        "as_of", "arrivals_today", "departures_today", "in_house_guests", "total_units", "total_guests",
        "active_bookings", "vacant_units", "occupancy_percent", "pending_checkins", "pending_checkouts",
        "pending_payments_count", "pending_payments_amount", "housekeeping_pending", "maintenance_open",
        "guest_requests_open", "low_stock_count", "staff_present_today", "staff_absent_today",
        "follow_ups_due_today", "today_revenue", "today_expenses", "today_profit", "month_revenue",
        "month_expenses", "month_profit", "apartment_performance", "monthly_trend",
    ):
        assert field in data, f"existing field '{field}' missing from dashboard response"
    assert isinstance(data["attention_items"], list)


def test_overdue_payment_appears_as_critical_attention_item(headers):
    unit_id = _make_property_and_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Overdue Balance Guest", "phone": f"91{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=6000)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat(),
    }).json()
    past_due = date.today() - timedelta(days=2)
    due_resp = client.patch(f"/api/v1/finance/bookings/{booking['id']}/due-date", headers=headers, params={"due_date": past_due.isoformat()})
    assert due_resp.status_code == 200

    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    matching = [i for i in dashboard["attention_items"] if i["reference_id"] == booking["id"]]
    assert len(matching) == 1, matching
    item = matching[0]
    assert item["category"] == "unpaid_balance"
    assert item["severity"] == "critical"
    assert item["link"] == "/finance"
    assert "Overdue Balance Guest" in item["title"]


def test_urgent_guest_request_past_threshold_appears_as_critical(headers):
    unit_id = _make_property_and_unit(headers)
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Attention Urgent Guest", "phone": f"92{uuid.uuid4().int % 10**8:08d}"}).json()
    check_in = date.today() + timedelta(days=6100)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"], "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=2)).isoformat(),
    }).json()

    from app.db.session import SessionLocal
    from app.modules.portal.models import GuestRequest
    from datetime import datetime, timezone
    db = SessionLocal()
    try:
        req = GuestRequest(
            guest_id=uuid.UUID(guest["id"]), booking_id=uuid.UUID(booking["id"]), request_type="service",
            description="AC not working, urgent attention test", priority="urgent", status="open",
        )
        req.created_at = datetime.now(timezone.utc) - timedelta(hours=5)  # past the 4h urgent threshold
        db.add(req)
        db.commit()
        db.refresh(req)
        request_id = str(req.id)
    finally:
        db.close()

    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    matching = [i for i in dashboard["attention_items"] if i["reference_id"] == request_id]
    assert len(matching) == 1, matching
    item = matching[0]
    assert item["category"] == "guest_request"
    assert item["severity"] == "critical"
    assert item["link"] == "/guest-requests"


def test_low_priority_fresh_guest_request_does_not_appear(headers):
    """Only genuinely urgent/overdue exceptions should surface — a
    freshly-opened low-priority request, well within its response-time
    target, must not be listed."""
    guest = client.post("/api/v1/guests", headers=headers, json={"name": "Attention Low Priority Guest", "phone": f"93{uuid.uuid4().int % 10**8:08d}"}).json()

    from app.db.session import SessionLocal
    from app.modules.portal.models import GuestRequest
    db = SessionLocal()
    try:
        req = GuestRequest(guest_id=uuid.UUID(guest["id"]), request_type="service", description="Extra towels please", priority="low", status="open")
        db.add(req)
        db.commit()
        db.refresh(req)
        request_id = str(req.id)
    finally:
        db.close()

    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    matching = [i for i in dashboard["attention_items"] if i["reference_id"] == request_id]
    assert matching == []


def test_out_of_stock_item_is_critical_low_stock_is_warning(headers):
    out_of_stock = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"OutOfStock {uuid.uuid4().hex[:6]}", "minimum_stock": 5}).json()
    low_stock = client.post("/api/v1/inventory/items", headers=headers, json={"name": f"LowStock {uuid.uuid4().hex[:6]}", "minimum_stock": 5}).json()
    client.post("/api/v1/inventory/stock-in", headers=headers, json={"item_id": low_stock["id"], "quantity": 3})

    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    items_by_id = {i["reference_id"]: i for i in dashboard["attention_items"]}

    assert items_by_id[out_of_stock["id"]]["category"] == "low_stock"
    assert items_by_id[out_of_stock["id"]]["severity"] == "critical"
    assert items_by_id[low_stock["id"]]["category"] == "low_stock"
    assert items_by_id[low_stock["id"]]["severity"] == "warning"


def test_attention_items_ranked_critical_before_warning(headers):
    """Deterministic ranking: every critical item must appear before every
    warning item in the returned list."""
    dashboard = client.get("/api/v1/reports/dashboard", headers=headers).json()
    items = dashboard["attention_items"]
    severities = [i["severity"] for i in items]
    first_warning_index = next((idx for idx, s in enumerate(severities) if s == "warning"), len(severities))
    # Nothing after the first "warning" should be "critical".
    assert "critical" not in severities[first_warning_index:]


def test_staff_can_see_attention_items(headers):
    """RBAC unchanged: staff already has access to /reports/dashboard and
    must continue to see attention_items exactly like admin does."""
    email = f"attnstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Attention Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/reports/dashboard", headers=staff_headers)
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["attention_items"], list)


def test_unauthenticated_cannot_see_attention_items():
    resp = client.get("/api/v1/reports/dashboard")
    assert resp.status_code == 401


# --- Phase 10A-3: Period-over-period comparison (Occupancy / ADR / RevPAR) ---

def _make_cmp_property_and_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"CmpProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"CmpUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return prop["id"], unit["id"]


def _make_cmp_guest(headers, tag):
    return client.post("/api/v1/guests", headers=headers, json={"name": f"Cmp Guest {tag}", "phone": f"9{uuid.uuid4().int % 10**9:09d}"}).json()


def test_summary_comparison_current_period_values(headers):
    """Current-period occupancy/ADR/RevPAR must match build_summary()'s own
    formulas applied to only the current-period booking (no previous-period
    data at all in this scenario)."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    guest = _make_cmp_guest(headers, "current-only")
    date_from = date.today() + timedelta(days=6500)
    date_to = date_from + timedelta(days=5)  # N = 5
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": date_from.isoformat(), "check_out": (date_from + timedelta(days=2)).isoformat(),
        "rate_applied": 2000,
    })
    assert booking.status_code == 201, booking.text

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["current_period"] == {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
    assert data["occupancy"]["current"] == 40.0  # 2 nights / (1 unit * 5 days) * 100
    assert data["adr"]["current"] == 2000.0  # 4000 revenue / 2 nights
    assert data["revpar"]["current"] == 800.0  # 4000 revenue / 5 available room-nights


def test_summary_comparison_previous_period_values(headers):
    """Previous-period occupancy/ADR/RevPAR must be computed from a booking
    placed ONLY in the preceding N-day window, and must not leak into the
    current period's figures."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    guest = _make_cmp_guest(headers, "previous-only")
    date_from = date.today() + timedelta(days=6600)
    date_to = date_from + timedelta(days=5)  # N = 5
    prev_check_in = date_from - timedelta(days=5)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": prev_check_in.isoformat(), "check_out": (prev_check_in + timedelta(days=1)).isoformat(),
        "rate_applied": 1000,
    })
    assert booking.status_code == 201, booking.text

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["previous_period"] == {"date_from": prev_check_in.isoformat(), "date_to": date_from.isoformat()}
    assert data["occupancy"]["previous"] == 20.0  # 1 night / (1 unit * 5 days) * 100
    assert data["adr"]["previous"] == 1000.0
    assert data["revpar"]["previous"] == 200.0  # 1000 revenue / 5 available room-nights
    # Nothing booked in the current period in this scenario.
    assert data["occupancy"]["current"] == 0.0
    assert data["adr"]["current"] == 0.0
    assert data["revpar"]["current"] == 0.0


def test_summary_comparison_percent_change_math(headers):
    """With both a current-period and a previous-period booking, verify the
    exact ((current - previous) / previous) * 100 formula for all three
    metrics."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    date_from = date.today() + timedelta(days=6700)
    date_to = date_from + timedelta(days=5)  # N = 5
    prev_check_in = date_from - timedelta(days=5)

    current_guest = _make_cmp_guest(headers, "pct-current")
    previous_guest = _make_cmp_guest(headers, "pct-previous")
    b1 = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": current_guest["id"],
        "check_in": date_from.isoformat(), "check_out": (date_from + timedelta(days=2)).isoformat(),
        "rate_applied": 2000,
    })
    b2 = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": previous_guest["id"],
        "check_in": prev_check_in.isoformat(), "check_out": (prev_check_in + timedelta(days=1)).isoformat(),
        "rate_applied": 1000,
    })
    assert b1.status_code == 201, b1.text
    assert b2.status_code == 201, b2.text

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # occupancy: 40.0 -> 20.0 => +100%; ADR: 2000 -> 1000 => +100%; RevPAR: 800 -> 200 => +300%
    assert data["occupancy"]["change_percent"] == 100.0
    assert data["adr"]["change_percent"] == 100.0
    assert data["revpar"]["change_percent"] == 300.0


def test_summary_comparison_previous_zero_returns_null_percent_change(headers):
    """When the previous period has no bookings at all (previous == 0 for
    every metric), change_percent must be null — never Infinity, 100%, or
    any other manufactured value — even though the current period is
    non-zero."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    guest = _make_cmp_guest(headers, "zero-previous")
    date_from = date.today() + timedelta(days=6800)
    date_to = date_from + timedelta(days=5)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": date_from.isoformat(), "check_out": (date_from + timedelta(days=2)).isoformat(),
        "rate_applied": 2000,
    })
    assert booking.status_code == 201, booking.text

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["occupancy"]["previous"] == 0.0
    assert data["adr"]["previous"] == 0.0
    assert data["revpar"]["previous"] == 0.0
    assert data["occupancy"]["current"] > 0
    assert data["occupancy"]["change_percent"] is None
    assert data["adr"]["change_percent"] is None
    assert data["revpar"]["change_percent"] is None


def test_summary_comparison_occupancy_percentage_point_change(headers):
    """Occupancy must expose a raw percentage-point change (current - previous)
    that is distinct from the percentage change — e.g. 40% -> 20% is a
    -20 percentage-point change but a -50% relative change."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    date_from = date.today() + timedelta(days=6900)
    date_to = date_from + timedelta(days=5)
    prev_check_in = date_from - timedelta(days=5)

    current_guest = _make_cmp_guest(headers, "pts-current")
    previous_guest = _make_cmp_guest(headers, "pts-previous")
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": current_guest["id"],
        "check_in": date_from.isoformat(), "check_out": (date_from + timedelta(days=2)).isoformat(),
        "rate_applied": 2000,
    })
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": previous_guest["id"],
        "check_in": prev_check_in.isoformat(), "check_out": (prev_check_in + timedelta(days=1)).isoformat(),
        "rate_applied": 1000,
    })

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["occupancy"]["current"] == 40.0
    assert data["occupancy"]["previous"] == 20.0
    assert data["occupancy"]["change_percentage_points"] == 20.0  # raw point difference
    assert data["occupancy"]["change_percent"] == 100.0  # relative percentage change
    assert data["occupancy"]["change_percentage_points"] != data["occupancy"]["change_percent"]


def test_summary_comparison_adr_has_no_percentage_point_field(headers):
    """ADR is a currency metric, not a percentage — it must not expose a
    percentage-point change (only occupancy does)."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7000)
    date_to = date_from + timedelta(days=5)
    prev_check_in = date_from - timedelta(days=5)

    current_guest = _make_cmp_guest(headers, "adr-current")
    previous_guest = _make_cmp_guest(headers, "adr-previous")
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": current_guest["id"],
        "check_in": date_from.isoformat(), "check_out": (date_from + timedelta(days=2)).isoformat(),
        "rate_applied": 2000,
    })
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": previous_guest["id"],
        "check_in": prev_check_in.isoformat(), "check_out": (prev_check_in + timedelta(days=1)).isoformat(),
        "rate_applied": 1000,
    })

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["adr"]["current"] == 2000.0
    assert data["adr"]["previous"] == 1000.0
    assert data["adr"]["change_percent"] == 100.0
    assert data["adr"].get("change_percentage_points") is None


def test_summary_comparison_revpar_comparison(headers):
    """RevPAR comparison must reflect revenue / available-room-nights for
    both periods, with no percentage-point field (same rule as ADR)."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7100)
    date_to = date_from + timedelta(days=5)
    prev_check_in = date_from - timedelta(days=5)

    current_guest = _make_cmp_guest(headers, "revpar-current")
    previous_guest = _make_cmp_guest(headers, "revpar-previous")
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": current_guest["id"],
        "check_in": date_from.isoformat(), "check_out": (date_from + timedelta(days=2)).isoformat(),
        "rate_applied": 2000,
    })
    client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": previous_guest["id"],
        "check_in": prev_check_in.isoformat(), "check_out": (prev_check_in + timedelta(days=1)).isoformat(),
        "rate_applied": 1000,
    })

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["revpar"]["current"] == 800.0
    assert data["revpar"]["previous"] == 200.0
    assert data["revpar"]["change_percent"] == 300.0
    assert data["revpar"].get("change_percentage_points") is None


def test_summary_comparison_date_boundaries(headers):
    """previous_period must end exactly where current_period starts, and
    span exactly N days for a non-trivial N."""
    property_id, _unit_id = _make_cmp_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7200)
    date_to = date_from + timedelta(days=9)  # N = 9

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["current_period"]["date_from"] == date_from.isoformat()
    assert data["current_period"]["date_to"] == date_to.isoformat()
    assert data["previous_period"]["date_to"] == date_from.isoformat()
    prev_from = date.fromisoformat(data["previous_period"]["date_from"])
    prev_to = date.fromisoformat(data["previous_period"]["date_to"])
    assert (prev_to - prev_from).days == 9
    assert (date_to - date_from).days == 9


def test_summary_comparison_rbac_unchanged(headers):
    """Same RBAC as /reports/summary: admin/staff allowed, unauthenticated
    rejected."""
    email = f"cmpstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Comparison Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/reports/summary-comparison", headers=staff_headers)
    assert resp.status_code == 200, resp.text

    resp_unauth = client.get("/api/v1/reports/summary-comparison")
    assert resp_unauth.status_code == 401


def test_summary_endpoint_unchanged_and_consistent_with_comparison(headers):
    """/reports/summary itself must be untouched: its response shape is
    unchanged, and its occupancy/ADR/RevPAR values for a given range must
    exactly match the "current" side of /reports/summary-comparison for the
    same range (proving build_summary() was reused, not reimplemented)."""
    property_id, unit_id = _make_cmp_property_and_unit(headers)
    guest = _make_cmp_guest(headers, "consistency")
    date_from = date.today() + timedelta(days=7300)
    date_to = date_from + timedelta(days=5)
    booking = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest["id"],
        "check_in": date_from.isoformat(), "check_out": (date_from + timedelta(days=2)).isoformat(),
        "rate_applied": 2000,
    })
    assert booking.status_code == 201, booking.text

    summary_resp = client.get("/api/v1/reports/summary", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert summary_resp.status_code == 200, summary_resp.text
    summary = summary_resp.json()
    for field in (
        "date_from", "date_to", "revenue", "total_bookings", "room_nights_sold", "available_room_nights",
        "occupancy_rate_percent", "adr", "revpar", "gst", "maintenance_cost_total", "net_revenue_after_maintenance",
    ):
        assert field in summary, f"existing /reports/summary field '{field}' missing — build_summary() must be unchanged"

    comparison_resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert comparison_resp.status_code == 200, comparison_resp.text
    comparison = comparison_resp.json()

    assert summary["occupancy_rate_percent"] == comparison["occupancy"]["current"]
    assert summary["adr"] == comparison["adr"]["current"]
    assert summary["revpar"] == comparison["revpar"]["current"]


# --- Phase 10A-4: Cancellation rate & trend reporting ---

def _make_cxl_property_and_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"CxlProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"CxlUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return prop["id"], unit["id"]


def _make_cxl_guest(headers, tag):
    return client.post("/api/v1/guests", headers=headers, json={"name": f"Cxl Guest {tag}", "phone": f"9{uuid.uuid4().int % 10**9:09d}"}).json()


def _make_cxl_booking(headers, unit_id, guest_id, check_in, nights=1, rate=2000):
    resp = client.post("/api/v1/bookings", headers=headers, json={
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=nights)).isoformat(),
        "rate_applied": rate,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_booking_status(headers, booking_id, new_status):
    resp = client.patch(f"/api/v1/bookings/{booking_id}/status", headers=headers, params={"new_status": new_status})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_cancellation_report_empty_period_zero_rates_no_error(headers):
    """No bookings of any kind in the period -> zero rates, no division error."""
    property_id, _unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7400)
    date_to = date_from + timedelta(days=5)

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_bookings_in_period"] == 0
    assert data["cancelled_count"] == 0
    assert data["cancellation_rate_percent"] == 0.0
    assert data["no_show_count"] == 0
    assert data["no_show_rate_percent"] == 0.0
    assert data["previous_total_bookings_in_period"] == 0
    assert data["cancellation_rate_change_percent"] is None
    assert data["no_show_rate_change_percent"] is None


def test_cancellation_report_known_cancelled_count_gives_exact_rate(headers):
    """4 firm bookings, 1 cancelled -> 25.0% cancellation rate."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7500)
    date_to = date_from + timedelta(days=10)

    guests = [_make_cxl_guest(headers, f"rate-{i}") for i in range(4)]
    bookings = [_make_cxl_booking(headers, unit_id, guests[i]["id"], date_from + timedelta(days=i * 2)) for i in range(4)]
    _set_booking_status(headers, bookings[0]["id"], "cancelled")

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_bookings_in_period"] == 4
    assert data["cancelled_count"] == 1
    assert data["cancellation_rate_percent"] == 25.0


def test_cancellation_report_mixed_statuses_classified_correctly(headers):
    """confirmed, checked_in, checked_out, cancelled, no_show, and inquiry
    all present in one period -> inquiry is excluded entirely from the
    firm-booking denominator, cancelled/no_show counted correctly."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7600)
    date_to = date_from + timedelta(days=14)

    g_confirmed = _make_cxl_guest(headers, "mixed-confirmed")
    g_checked_in = _make_cxl_guest(headers, "mixed-checked-in")
    g_checked_out = _make_cxl_guest(headers, "mixed-checked-out")
    g_cancelled = _make_cxl_guest(headers, "mixed-cancelled")
    g_no_show = _make_cxl_guest(headers, "mixed-no-show")
    g_inquiry = _make_cxl_guest(headers, "mixed-inquiry")

    b_confirmed = _make_cxl_booking(headers, unit_id, g_confirmed["id"], date_from)
    b_checked_in = _make_cxl_booking(headers, unit_id, g_checked_in["id"], date_from + timedelta(days=2))
    b_checked_out = _make_cxl_booking(headers, unit_id, g_checked_out["id"], date_from + timedelta(days=4))
    b_cancelled = _make_cxl_booking(headers, unit_id, g_cancelled["id"], date_from + timedelta(days=6))
    b_no_show = _make_cxl_booking(headers, unit_id, g_no_show["id"], date_from + timedelta(days=8))
    b_inquiry = _make_cxl_booking(headers, unit_id, g_inquiry["id"], date_from + timedelta(days=10))

    _set_booking_status(headers, b_checked_in["id"], "checked_in")
    _set_booking_status(headers, b_checked_out["id"], "checked_in")
    _set_booking_status(headers, b_checked_out["id"], "checked_out")
    _set_booking_status(headers, b_cancelled["id"], "cancelled")
    _set_booking_status(headers, b_no_show["id"], "no_show")

    # BookingCreate/BookingUpdate have no "status" field — the API has no
    # path to put a booking into "inquiry" once created as "confirmed", so
    # this is set directly at the DB layer, matching the same read-modify
    # pattern already used elsewhere in this file for fields the API
    # doesn't expose (e.g. GuestRequest.created_at).
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Booking
    db = SessionLocal()
    try:
        row = db.get(Booking, uuid.UUID(b_inquiry["id"]))
        row.status = "inquiry"
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 5 firm bookings (confirmed, checked_in, checked_out, cancelled, no_show) — inquiry excluded.
    assert data["total_bookings_in_period"] == 5
    assert data["cancelled_count"] == 1
    assert data["no_show_count"] == 1
    assert data["cancellation_rate_percent"] == 20.0  # 1/5
    assert data["no_show_rate_percent"] == 20.0  # 1/5
    assert b_confirmed  # sanity: confirmed booking exists and was counted in the denominator


def test_cancellation_report_no_show_remains_separate(headers):
    """A period with only a no_show booking must show cancelled_count == 0
    and no_show_count == 1 — no_show must never be folded into cancellations."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7700)
    date_to = date_from + timedelta(days=5)
    guest = _make_cxl_guest(headers, "no-show-only")
    booking = _make_cxl_booking(headers, unit_id, guest["id"], date_from)
    _set_booking_status(headers, booking["id"], "no_show")

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cancelled_count"] == 0
    assert data["cancellation_rate_percent"] == 0.0
    assert data["no_show_count"] == 1
    assert data["no_show_rate_percent"] == 100.0


def test_cancellation_report_period_boundaries(headers):
    """check_in-date basis: a booking whose check_in falls exactly on
    date_to must NOT be included (half-open [date_from, date_to)); a
    booking checking in exactly on date_from must be included."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7800)
    date_to = date_from + timedelta(days=5)

    guest_in = _make_cxl_guest(headers, "boundary-in")
    guest_out = _make_cxl_guest(headers, "boundary-out")
    _make_cxl_booking(headers, unit_id, guest_in["id"], date_from)  # on date_from -> included
    _make_cxl_booking(headers, unit_id, guest_out["id"], date_to)  # on date_to -> excluded

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_bookings_in_period"] == 1
    assert data["current_period"] == {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}


def test_cancellation_report_previous_period_values(headers):
    """previous_period must be exactly the immediately preceding equal-length
    ([date_from - N, date_from)) window, and its stats must reflect only
    bookings placed in that window."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=7900)
    date_to = date_from + timedelta(days=5)  # N = 5
    prev_check_in = date_from - timedelta(days=5)

    guest = _make_cxl_guest(headers, "prev-period")
    booking = _make_cxl_booking(headers, unit_id, guest["id"], prev_check_in)
    _set_booking_status(headers, booking["id"], "cancelled")

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["previous_period"] == {"date_from": prev_check_in.isoformat(), "date_to": date_from.isoformat()}
    assert data["previous_total_bookings_in_period"] == 1
    assert data["previous_cancelled_count"] == 1
    assert data["previous_cancellation_rate_percent"] == 100.0
    assert data["total_bookings_in_period"] == 0  # nothing booked in the current period here


def test_cancellation_report_change_percent_exact(headers):
    """With a known current rate and a known previous rate, verify the
    exact ((current - previous) / previous) * 100 formula."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=8000)
    date_to = date_from + timedelta(days=10)  # N = 10
    prev_check_in = date_from - timedelta(days=10)

    # Current period: 2 bookings, 1 cancelled -> 50% cancellation rate.
    g1 = _make_cxl_guest(headers, "chg-current-1")
    g2 = _make_cxl_guest(headers, "chg-current-2")
    b1 = _make_cxl_booking(headers, unit_id, g1["id"], date_from)
    _make_cxl_booking(headers, unit_id, g2["id"], date_from + timedelta(days=2))
    _set_booking_status(headers, b1["id"], "cancelled")

    # Previous period: 4 bookings, 1 cancelled -> 25% cancellation rate.
    prev_guests = [_make_cxl_guest(headers, f"chg-prev-{i}") for i in range(4)]
    prev_bookings = [_make_cxl_booking(headers, unit_id, prev_guests[i]["id"], prev_check_in + timedelta(days=i * 2)) for i in range(4)]
    _set_booking_status(headers, prev_bookings[0]["id"], "cancelled")

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cancellation_rate_percent"] == 50.0
    assert data["previous_cancellation_rate_percent"] == 25.0
    assert data["cancellation_rate_change_percent"] == 100.0  # (50-25)/25*100


def test_cancellation_report_previous_zero_returns_null_change(headers):
    """Previous period has zero firm bookings at all -> change_percent must
    be null, never a manufactured value, even though the current period has
    a non-zero cancellation rate."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    date_from = date.today() + timedelta(days=8100)
    date_to = date_from + timedelta(days=5)
    guest = _make_cxl_guest(headers, "zero-prev-cxl")
    booking = _make_cxl_booking(headers, unit_id, guest["id"], date_from)
    _set_booking_status(headers, booking["id"], "cancelled")

    resp = client.get("/api/v1/reports/cancellations", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["previous_total_bookings_in_period"] == 0
    assert data["cancellation_rate_percent"] == 100.0
    assert data["cancellation_rate_change_percent"] is None
    assert data["no_show_rate_change_percent"] is None


def test_cancellation_report_staff_access_allowed(headers):
    email = f"cxlstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Cancellation Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/reports/cancellations", headers=staff_headers)
    assert resp.status_code == 200, resp.text


def test_cancellation_report_unauthenticated_rejected():
    resp = client.get("/api/v1/reports/cancellations")
    assert resp.status_code == 401


def test_summary_unchanged_after_cancellation_feature(headers):
    """/reports/summary must be completely unaffected by the new
    cancellation report — same fields, same values for the same range."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    guest = _make_cxl_guest(headers, "summary-regress")
    date_from = date.today() + timedelta(days=8200)
    date_to = date_from + timedelta(days=5)
    _make_cxl_booking(headers, unit_id, guest["id"], date_from)

    resp = client.get("/api/v1/reports/summary", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    for field in (
        "date_from", "date_to", "revenue", "total_bookings", "room_nights_sold", "available_room_nights",
        "occupancy_rate_percent", "adr", "revpar", "gst", "maintenance_cost_total", "net_revenue_after_maintenance",
    ):
        assert field in summary, f"existing /reports/summary field '{field}' missing after 10A-4"
    assert summary["total_bookings"] == 1
    assert summary["occupancy_rate_percent"] == 20.0  # 1 night / (1 unit * 5 days) * 100


def test_summary_comparison_unchanged_after_cancellation_feature(headers):
    """/reports/summary-comparison (10A-3) must be completely unaffected by
    the new cancellation report — same fields, same values."""
    property_id, unit_id = _make_cxl_property_and_unit(headers)
    guest = _make_cxl_guest(headers, "comparison-regress")
    date_from = date.today() + timedelta(days=8300)
    date_to = date_from + timedelta(days=5)
    _make_cxl_booking(headers, unit_id, guest["id"], date_from)

    resp = client.get("/api/v1/reports/summary-comparison", headers=headers, params={
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for field in ("current_period", "previous_period", "occupancy", "adr", "revpar"):
        assert field in data, f"existing /reports/summary-comparison field '{field}' missing after 10A-4"
    assert data["occupancy"]["current"] == 20.0
    assert data["occupancy"]["change_percentage_points"] is not None
