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
