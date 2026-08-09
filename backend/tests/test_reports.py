"""
Tests for the reporting module: summary KPIs, revenue series, unit
performance, GST reporting, and PDF/Excel export.
Run with: pytest tests/test_reports.py -v
"""
import uuid

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


def _make_property(headers, state_code="33"):
    resp = client.post(
        "/api/v1/properties",
        headers=headers,
        json={"name": f"ReportProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": state_code},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _make_unit(headers, property_id, base_rate=2000):
    resp = client.post(
        "/api/v1/units",
        headers=headers,
        json={"property_id": property_id, "name": f"ReportUnit {uuid.uuid4().hex[:6]}", "base_rate": base_rate},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _make_guest(headers, state_code="33"):
    resp = client.post("/api/v1/guests", headers=headers, json={"name": "Report Guest", "phone": "9333333333"})
    guest_id = resp.json()["id"]
    from app.db.session import SessionLocal
    from app.modules.bookings.models import Guest
    db = SessionLocal()
    g = db.get(Guest, uuid.UUID(guest_id))
    g.state_code = state_code
    db.commit()
    db.close()
    return guest_id


def _make_booking(headers, unit_id, guest_id, check_in, check_out):
    resp = client.post(
        "/api/v1/bookings",
        headers=headers,
        json={"unit_id": unit_id, "guest_id": guest_id, "check_in": check_in, "check_out": check_out},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_summary_report_math_is_correct(headers):
    """Verify occupancy/ADR/RevPAR/revenue math against hand-calculated values."""
    property_id = _make_property(headers)
    unit1 = _make_unit(headers, property_id, base_rate=2000)
    unit2 = _make_unit(headers, property_id, base_rate=2000)
    guest = _make_guest(headers)

    _make_booking(headers, unit1, guest, "2028-01-01", "2028-01-04")  # 3 nights, 6000 revenue
    _make_booking(headers, unit2, guest, "2028-01-05", "2028-01-07")  # 2 nights, 4000 revenue

    resp = client.get(
        "/api/v1/reports/summary",
        headers=headers,
        params={"date_from": "2028-01-01", "date_to": "2028-02-01", "property_id": property_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["revenue"]["total"] == 10000.0
    assert data["total_bookings"] == 2
    assert data["room_nights_sold"] == 5
    assert data["available_room_nights"] == 2 * 31  # 2 units x 31 days in January
    assert data["adr"] == 2000.0
    expected_occupancy = round(5 / 62 * 100, 2)
    assert data["occupancy_rate_percent"] == expected_occupancy
    expected_revpar = round(10000 / 62, 2)
    assert data["revpar"] == expected_revpar


def test_cancelled_bookings_excluded_from_revenue(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id, base_rate=1500)
    guest = _make_guest(headers)
    booking_id = _make_booking(headers, unit_id, guest, "2028-02-01", "2028-02-03")

    # Cancel it — confirmed -> cancelled is a valid transition
    cancel = client.patch(f"/api/v1/bookings/{booking_id}/status", headers=headers, params={"new_status": "cancelled"})
    assert cancel.status_code == 200

    resp = client.get(
        "/api/v1/reports/summary",
        headers=headers,
        params={"date_from": "2028-02-01", "date_to": "2028-03-01", "property_id": property_id},
    )
    data = resp.json()
    assert data["total_bookings"] == 0
    assert data["revenue"]["total"] == 0.0


def test_gst_report_matches_generated_invoices(headers):
    property_id = _make_property(headers, state_code="33")
    unit_id = _make_unit(headers, property_id, base_rate=2000)  # 5% slab
    guest = _make_guest(headers, state_code="33")  # same state -> CGST/SGST
    booking_id = _make_booking(headers, unit_id, guest, "2028-03-01", "2028-03-04")  # 3 nights = 6000 subtotal

    invoice = client.post(f"/api/v1/invoices/generate/{booking_id}", headers=headers)
    assert invoice.status_code == 201
    issued_date = invoice.json()["issued_at"][:10]  # invoice is issued "now", not at stay date

    resp = client.get(
        "/api/v1/reports/gst",
        headers=headers,
        params={"date_from": issued_date, "date_to": "2099-01-01"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cgst"] >= 150.0  # may include other invoices issued same day by other tests
    assert data["invoice_count"] >= 1


def test_unit_performance_breaks_down_by_unit(headers):
    property_id = _make_property(headers)
    unit1 = _make_unit(headers, property_id, base_rate=1000)
    unit2 = _make_unit(headers, property_id, base_rate=3000)
    guest = _make_guest(headers)

    _make_booking(headers, unit1, guest, "2028-04-01", "2028-04-03")  # 2 nights x 1000 = 2000
    _make_booking(headers, unit2, guest, "2028-04-01", "2028-04-02")  # 1 night x 3000 = 3000

    resp = client.get(
        "/api/v1/reports/unit-performance",
        headers=headers,
        params={"date_from": "2028-04-01", "date_to": "2028-05-01", "property_id": property_id},
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    # sorted by revenue descending
    assert rows[0]["revenue"] == 3000.0
    assert rows[1]["revenue"] == 2000.0


def test_revenue_series_grouped_by_check_in_date(headers):
    property_id = _make_property(headers)
    unit_id = _make_unit(headers, property_id, base_rate=1000)
    guest = _make_guest(headers)

    _make_booking(headers, unit_id, guest, "2028-05-01", "2028-05-02")
    _make_booking(headers, unit_id, guest, "2028-05-03", "2028-05-04")

    resp = client.get(
        "/api/v1/reports/revenue-series",
        headers=headers,
        params={"date_from": "2028-05-01", "date_to": "2028-06-01", "property_id": property_id},
    )
    assert resp.status_code == 200
    points = resp.json()
    assert len(points) == 2
    assert points[0]["date"] == "2028-05-01"
    assert points[1]["date"] == "2028-05-03"


def test_invalid_date_range_rejected(headers):
    resp = client.get(
        "/api/v1/reports/summary",
        headers=headers,
        params={"date_from": "2028-06-01", "date_to": "2028-01-01"},  # to before from
    )
    assert resp.status_code == 400


def test_export_summary_pdf(headers):
    resp = client.get(
        "/api/v1/reports/export",
        headers=headers,
        params={"report": "summary", "format": "pdf", "date_from": "2028-01-01", "date_to": "2028-02-01"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


def test_export_summary_xlsx(headers):
    resp = client.get(
        "/api/v1/reports/export",
        headers=headers,
        params={"report": "summary", "format": "xlsx", "date_from": "2028-01-01", "date_to": "2028-02-01"},
    )
    assert resp.status_code == 200
    assert "spreadsheet" in resp.headers["content-type"]
    assert resp.content[:2] == b"PK"  # xlsx is a zip archive


def test_export_unit_performance_pdf(headers):
    resp = client.get(
        "/api/v1/reports/export",
        headers=headers,
        params={"report": "unit_performance", "format": "pdf", "date_from": "2028-04-01", "date_to": "2028-05-01"},
    )
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_export_invalid_report_rejected(headers):
    resp = client.get("/api/v1/reports/export", headers=headers, params={"report": "nonsense", "format": "pdf"})
    assert resp.status_code == 400


def test_export_gst_pdf_unsupported(headers):
    resp = client.get(
        "/api/v1/reports/export",
        headers=headers,
        params={"report": "gst", "format": "pdf", "date_from": "2028-01-01", "date_to": "2028-02-01"},
    )
    assert resp.status_code == 400


def test_staff_can_access_reports():
    admin_login = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    email = f"report_staff_{uuid.uuid4().hex[:6]}@sriapartmenthomestay.com"
    client.post(
        "/api/v1/auth/register",
        headers=admin_headers,
        json={"name": "Report Staff", "email": email, "password": "Pass12345!", "role": "staff"},
    )
    staff_login = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass12345!"})
    staff_headers = {"Authorization": f"Bearer {staff_login.json()['access_token']}"}

    resp = client.get("/api/v1/reports/summary", headers=staff_headers)
    assert resp.status_code == 200


def test_unauthenticated_cannot_access_reports():
    assert client.get("/api/v1/reports/summary").status_code == 401
    assert client.get("/api/v1/reports/export", params={"report": "summary", "format": "pdf"}).status_code == 401


def test_default_date_range_is_current_month(headers):
    """No date params should default to the current calendar month, not error."""
    resp = client.get("/api/v1/reports/summary", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["date_from"] is not None
    assert data["date_to"] is not None
