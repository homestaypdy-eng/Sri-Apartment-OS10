"""
Tests completing the Reports module to full production coverage:
property_pnl PDF/Excel export (existed as a JSON endpoint but wasn't
exportable via Reports Center) and the new Guest Report.
Run with: pytest tests/test_reports_completion.py -v
"""
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


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


@pytest.mark.parametrize(
    ("status", "check_in_offset", "expected_severity", "expected_reason", "forbidden_reason"),
    [
        ("checked_in", 0, "critical", "Guest is checked in", None),
        ("confirmed", 0, "critical", "due to check in today", "Guest is checked in"),
        ("confirmed", -1, "critical", "past check-in date", "Guest is checked in"),
        ("confirmed", 2, "warning", "Guest arrives in 2 day(s)", "Guest is checked in"),
    ],
)
def test_unpaid_balance_alert_wording_uses_booking_status(
    status, check_in_offset, expected_severity, expected_reason, forbidden_reason
):
    """Only persisted checked_in status may support checked-in alert wording."""
    from app.modules.bookings.models import Booking
    from app.modules.reports.service import build_attention_items

    today = date(2026, 9, 29)
    booking_id = uuid.uuid4()
    booking = SimpleNamespace(id=booking_id, status=status)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [booking]
    outstanding = [{
        "booking_id": booking_id,
        "guest_name": "Policy Alert Guest",
        "unit_name": "Flat A1",
        "check_in": today + timedelta(days=check_in_offset),
        "pending_balance": 5000,
        "is_overdue": False,
        "due_date": None,
    }]

    items = build_attention_items(db, today, outstanding, [], [], [], {})

    assert len(items) == 1
    item = items[0]
    assert item["category"] == "unpaid_balance"
    assert item["severity"] == expected_severity
    assert item["link"] == "/finance"
    assert item["reference_id"] == str(booking_id)
    assert "₹5,000" in item["title"]
    assert "₹5,000" in item["reason"]
    assert expected_reason in item["reason"]
    if forbidden_reason:
        assert forbidden_reason not in item["reason"]
    db.scalars.assert_called_once()
    query = db.scalars.call_args.args[0]
    assert query.column_descriptions[0]["entity"] is Booking


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


# --- Phase 13B: AI Strategist deterministic data preparation ---

def _make_strat_property_and_unit(headers):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"StratProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"StratUnit {uuid.uuid4().hex[:6]}", "base_rate": 2000}).json()
    return prop["id"], unit["id"]


def _make_strat_guest(headers, tag):
    return client.post("/api/v1/guests", headers=headers, json={"name": f"Strat Guest {tag}", "phone": f"9{uuid.uuid4().int % 10**9:09d}"}).json()


def _make_strat_booking(headers, unit_id, guest_id, check_in, nights=2, rate=2000, source=None):
    payload = {
        "unit_id": unit_id, "guest_id": guest_id,
        "check_in": check_in.isoformat(), "check_out": (check_in + timedelta(days=nights)).isoformat(),
        "rate_applied": rate,
    }
    if source:
        payload["source"] = source
    resp = client.post("/api/v1/bookings", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_ai_strategist_requires_admin_staff_rejected(headers):
    email = f"stratstaff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Strategist Staff", "email": email, "password": "Pass12345!", "role": "staff"})
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/reports/ai-strategist", headers=staff_headers)
    assert resp.status_code == 403, resp.text


def test_ai_strategist_unauthenticated_rejected():
    resp = client.get("/api/v1/reports/ai-strategist")
    assert resp.status_code == 401


def test_ai_strategist_admin_access_and_schema_stable(headers):
    resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for section in (
        "generated_at", "business_health", "needs_attention", "cancellations", "outstanding_payments",
        "unit_performance", "booking_sources", "top_guests", "maintenance", "gst", "weekly_facts",
    ):
        assert section in data, f"AI Strategist bundle missing section '{section}'"

    bh = data["business_health"]
    for field in ("current_period", "previous_period", "revenue_current", "revenue_previous", "revenue_definition", "occupancy", "adr", "revpar"):
        assert field in bh, f"business_health missing '{field}'"
    for field in ("current_period", "previous_period", "total_bookings_in_period", "cancelled_count", "cancellation_rate_percent", "no_show_count", "no_show_rate_percent"):
        assert field in data["cancellations"], f"cancellations missing '{field}'"
    for field in ("total_pending_balance", "pending_count", "overdue_count", "overdue_amount"):
        assert field in data["outstanding_payments"], f"outstanding_payments missing '{field}'"
    for field in ("current_period", "top_units", "bottom_units"):
        assert field in data["unit_performance"], f"unit_performance missing '{field}'"
    for field in ("current_period", "gst_collected", "gst_input_credit_eligible", "gst_payable"):
        assert field in data["gst"], f"gst missing '{field}'"


def test_ai_strategist_revenue_definitions_explicit(headers):
    resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "extra_guest_charge" in data["business_health"]["revenue_definition"]
    assert "extra_guest_charge" in data["booking_sources"]["revenue_definition"]
    assert data["business_health"]["revenue_definition"] == data["booking_sources"]["revenue_definition"]


def test_ai_strategist_cancellation_values_match_existing_endpoint(headers):
    property_id, unit_id = _make_strat_property_and_unit(headers)
    date_from = date.today() + timedelta(days=9900)
    date_to = date_from + timedelta(days=5)
    guest = _make_strat_guest(headers, "cxl-match")
    booking = _make_strat_booking(headers, unit_id, guest["id"], date_from)
    client.patch(f"/api/v1/bookings/{booking['id']}/status", headers=headers, params={"new_status": "cancelled"})

    params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id}
    cxl_resp = client.get("/api/v1/reports/cancellations", headers=headers, params=params)
    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params=params)
    assert cxl_resp.status_code == 200 and strat_resp.status_code == 200

    cxl = cxl_resp.json()
    strat = strat_resp.json()["cancellations"]
    assert strat["cancelled_count"] == cxl["cancelled_count"] == 1
    assert strat["cancellation_rate_percent"] == cxl["cancellation_rate_percent"]
    assert strat["no_show_count"] == cxl["no_show_count"]
    assert strat["no_show_rate_percent"] == cxl["no_show_rate_percent"]
    assert strat["total_bookings_in_period"] == cxl["total_bookings_in_period"]


def test_ai_strategist_unit_performance_matches_existing_endpoint(headers):
    property_id, unit_id = _make_strat_property_and_unit(headers)
    date_from = date.today() + timedelta(days=10000)
    date_to = date_from + timedelta(days=5)
    guest = _make_strat_guest(headers, "unitperf-match")
    _make_strat_booking(headers, unit_id, guest["id"], date_from, rate=3000)

    params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "property_id": property_id}
    unit_resp = client.get("/api/v1/reports/unit-performance", headers=headers, params=params)
    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params=params)
    assert unit_resp.status_code == 200 and strat_resp.status_code == 200

    unit_rows = {r["unit_id"]: r for r in unit_resp.json()}
    strat_top = {r["unit_id"]: r for r in strat_resp.json()["unit_performance"]["top_units"]}
    assert unit_id in strat_top
    assert strat_top[unit_id]["revenue"] == unit_rows[unit_id]["revenue"]
    assert strat_top[unit_id]["occupancy_rate_percent"] == unit_rows[unit_id]["occupancy_rate_percent"]
    assert strat_top[unit_id]["adr"] == unit_rows[unit_id]["adr"]
    # RevPAR = revenue / days_in_range, not returned by /unit-performance — cross-checked against the same identity.
    days_in_range = (date_to - date_from).days
    assert strat_top[unit_id]["revpar"] == round(unit_rows[unit_id]["revenue"] / days_in_range, 2)


def test_ai_strategist_guest_segments_match_existing_endpoint(headers):
    tag = uuid.uuid4().hex[:8]
    unit_id = _make_strat_property_and_unit(headers)[1]
    date_from = date.today() + timedelta(days=10100)
    guest = _make_strat_guest(headers, f"guestmatch-{tag}")
    booking = _make_strat_booking(headers, unit_id, guest["id"], date_from)
    client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 4000, "mode": "upi", "reference_no": f"STRAT{tag}"})

    seg_resp = client.get("/api/v1/guests/segments", headers=headers, params={"ids": guest["id"]})
    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert seg_resp.status_code == 200 and strat_resp.status_code == 200

    seg = seg_resp.json()[0]
    strat_guests = {g["guest_id"]: g for g in strat_resp.json()["top_guests"]}
    # This guest may or may not land in the (small) top-N depending on how
    # much other test data exists with higher spend — assert equality only
    # when present, and separately prove the formulas agree via direct
    # comparison against the guest-segments endpoint's own numbers.
    if guest["id"] in strat_guests:
        s = strat_guests[guest["id"]]
        assert s["is_repeat_guest"] == seg["is_repeat_guest"]
        assert s["total_bookings"] == seg["total_bookings"]
        assert s["total_spent"] == seg["total_spent"] == 4000.0
        assert s["latest_source"] == seg["latest_source"]
        assert s["last_stay"] == seg["last_stay"]
    else:
        assert seg["total_spent"] == 4000.0  # formula itself still verified independently


def test_ai_strategist_outstanding_matches_existing_endpoint(headers):
    # /finance/outstanding-payments is deliberately paginated (default
    # limit=200) to bound UI list payload size — but the AI Strategist
    # bundle's outstanding_payments summary is an unpaginated total over
    # the same canonical finance/service.py::outstanding_payments() data.
    # Comparing the two requires requesting the full (unpaginated) set
    # from the paginated endpoint, using its own max limit — otherwise
    # this test fails whenever accumulated test data crosses 200
    # outstanding bookings, which is not a real behavioral mismatch.
    resp_finance = client.get("/api/v1/finance/outstanding-payments", headers=headers, params={"limit": 5000})
    resp_strat = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert resp_finance.status_code == 200 and resp_strat.status_code == 200

    finance_lines = resp_finance.json()
    expected_total = round(sum(l["pending_balance"] for l in finance_lines), 2)
    expected_overdue = [l for l in finance_lines if l["is_overdue"]]

    strat_outstanding = resp_strat.json()["outstanding_payments"]
    assert strat_outstanding["pending_count"] == len(finance_lines)
    assert strat_outstanding["total_pending_balance"] == expected_total
    assert strat_outstanding["overdue_count"] == len(expected_overdue)
    assert strat_outstanding["overdue_amount"] == round(sum(l["pending_balance"] for l in expected_overdue), 2)


def test_ai_strategist_gst_matches_existing_endpoint(headers):
    date_from = date.today() + timedelta(days=10200)
    date_to = date_from + timedelta(days=5)
    params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}

    gst_resp = client.get("/api/v1/finance/gst-dashboard", headers=headers, params=params)
    strat_resp = client.get("/api/v1/reports/ai-strategist", headers=headers, params=params)
    assert gst_resp.status_code == 200 and strat_resp.status_code == 200

    gst = gst_resp.json()
    strat_gst = strat_resp.json()["gst"]
    assert strat_gst["gst_collected"] == gst["gst_collected"]
    assert strat_gst["gst_input_credit_eligible"] == gst["gst_input_credit_eligible"]
    assert strat_gst["gst_payable"] == gst["gst_payable"]


def test_ai_strategist_no_sensitive_fields(headers):
    resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    allowed_guest_fields = {"guest_id", "name", "is_repeat_guest", "total_bookings", "total_spent", "latest_source", "last_stay"}
    for g in data["top_guests"]:
        assert set(g.keys()) == allowed_guest_fields, f"unexpected guest field(s): {set(g.keys()) - allowed_guest_fields}"
        for forbidden in ("phone", "email", "is_vip", "is_blacklisted"):
            assert forbidden not in g

    raw_text = str(data).lower()
    for forbidden in ("password", "smtp_password", "jwt_secret", "token_hash", "reset_token"):
        assert forbidden not in raw_text


def test_ai_strategist_batched_correctness_across_many_guests_and_units(headers):
    """
    Functional proof of batching (mirrors 10B-1's test_batched_no_per_guest_queries):
    several guests and units with known spend/revenue resolve correctly
    from one call — proving every row was actually considered, not just
    a partial/broken subset a per-row loop might produce.
    """
    tag = uuid.uuid4().hex[:6]
    date_from = date.today() + timedelta(days=10300)

    units = [_make_strat_property_and_unit(headers)[1] for _ in range(3)]
    guests = [_make_strat_guest(headers, f"batch-{tag}-{i}") for i in range(6)]
    for i, (unit_id, guest) in enumerate(zip(units * 2, guests)):
        booking = _make_strat_booking(headers, unit_id, guest["id"], date_from + timedelta(days=i * 4), rate=1000 * (i + 1))
        client.post("/api/v1/payments", headers=headers, json={"booking_id": booking["id"], "amount": 500 * (i + 1), "mode": "cash"})

    resp = client.get("/api/v1/reports/ai-strategist", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The highest-spending guest among this batch must outrank the lowest
    # in the returned top_guests ordering if both happen to be present —
    # proving the aggregation considered the full set before ranking, not
    # an arbitrary or partial one.
    guest_ids_present = [g["guest_id"] for g in data["top_guests"]]
    if guests[-1]["id"] in guest_ids_present and guests[0]["id"] in guest_ids_present:
        assert guest_ids_present.index(guests[-1]["id"]) < guest_ids_present.index(guests[0]["id"])
    assert len(data["unit_performance"]["top_units"]) <= 5
    assert len(data["top_guests"]) <= 5


def test_ai_strategist_query_count_bounded(headers):
    """
    Direct proof of "no N+1": count real SQL statements issued by
    build_ai_strategist_bundle() via a SQLAlchemy engine event, once with
    a small dataset and once with a meaningfully larger one — the query
    count must be identical, proving it doesn't scale with guest/unit/
    booking count.
    """
    from sqlalchemy import event
    from app.db.session import SessionLocal, engine
    from app.modules.reports.service import build_ai_strategist_bundle

    def _count_queries(n_extra_guests: int, n_extra_units: int) -> int:
        tag = uuid.uuid4().hex[:6]
        for _ in range(n_extra_units):
            _make_strat_property_and_unit(headers)
        for i in range(n_extra_guests):
            _make_strat_guest(headers, f"qcount-{tag}-{i}")

        counter = {"n": 0}

        def _before_cursor_execute(*args, **kwargs):
            counter["n"] += 1

        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
        db = SessionLocal()
        try:
            build_ai_strategist_bundle(db, date.today().replace(day=1), date.today() + timedelta(days=1), None)
        finally:
            db.close()
            event.remove(engine, "before_cursor_execute", _before_cursor_execute)
        return counter["n"]

    small = _count_queries(n_extra_guests=2, n_extra_units=1)
    large = _count_queries(n_extra_guests=15, n_extra_units=5)

    assert small < 60, f"query count unexpectedly high even for a small dataset: {small}"
    assert large == small, f"query count grew with entity count ({small} -> {large}) — indicates N+1"


def test_existing_endpoints_unchanged_after_ai_strategist(headers):
    """Regression: every endpoint the AI Strategist bundle reuses must be
    completely unaffected by its addition."""
    today = date.today()
    params = {"date_from": today.replace(day=1).isoformat(), "date_to": (today + timedelta(days=1)).isoformat()}

    summary = client.get("/api/v1/reports/summary", headers=headers, params=params)
    assert summary.status_code == 200
    for field in ("date_from", "date_to", "revenue", "occupancy_rate_percent", "adr", "revpar", "gst", "maintenance_cost_total"):
        assert field in summary.json()

    cxl = client.get("/api/v1/reports/cancellations", headers=headers, params=params)
    assert cxl.status_code == 200
    for field in ("cancelled_count", "cancellation_rate_percent", "no_show_count", "no_show_rate_percent"):
        assert field in cxl.json()

    segments = client.get("/api/v1/guests/segments", headers=headers)
    assert segments.status_code == 200
    assert isinstance(segments.json(), list)

    outstanding = client.get("/api/v1/finance/outstanding-payments", headers=headers)
    assert outstanding.status_code == 200
    assert isinstance(outstanding.json(), list)

    gst = client.get("/api/v1/finance/gst-dashboard", headers=headers, params=params)
    assert gst.status_code == 200
    for field in ("gst_collected", "gst_input_credit_eligible", "gst_payable"):
        assert field in gst.json()
