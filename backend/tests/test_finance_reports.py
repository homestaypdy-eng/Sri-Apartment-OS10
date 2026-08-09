"""
Tests for the finance report exports: Cash Book, Apartment P&L, Expense,
Purchase, Utility, Salary — both PDF and Excel formats.
Run with: pytest tests/test_finance_reports.py -v
"""
import uuid
from datetime import date

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


def _make_unit(headers, rate=2000):
    prop = client.post("/api/v1/properties", headers=headers, json={"name": f"RepProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers, json={"property_id": prop["id"], "name": f"RepUnit {uuid.uuid4().hex[:6]}", "base_rate": rate}).json()
    return unit["id"]


@pytest.mark.parametrize("report,fmt", [
    ("cashbook", "pdf"), ("cashbook", "xlsx"),
    ("expenses", "pdf"), ("expenses", "xlsx"),
    ("purchases", "pdf"), ("purchases", "xlsx"),
    ("utility", "pdf"), ("utility", "xlsx"),
])
def test_report_export_returns_valid_file(headers, report, fmt):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={"report": report, "format": fmt})
    assert resp.status_code == 200, resp.text
    assert len(resp.content) > 100
    if fmt == "pdf":
        assert resp.content[:4] == b"%PDF"


def test_apartment_pnl_export_requires_params(headers):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={"report": "apartment_pnl", "format": "pdf"})
    assert resp.status_code == 400


def test_apartment_pnl_export_works_with_params(headers):
    unit_id = _make_unit(headers)
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={
        "report": "apartment_pnl", "format": "pdf", "unit_id": unit_id, "period_year": 2026, "period_month": 7,
    })
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_salary_report_export(headers):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={
        "report": "salary", "format": "xlsx", "period_year": date.today().year, "period_month": date.today().month,
    })
    assert resp.status_code == 200
    assert len(resp.content) > 100


def test_invalid_report_type_rejected(headers):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={"report": "bogus", "format": "pdf"})
    assert resp.status_code == 400


def test_invalid_format_rejected(headers):
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={"report": "cashbook", "format": "docx"})
    assert resp.status_code == 400


def test_pdf_export_handles_special_characters_safely(headers):
    """
    Regression test for a real bug found in this pass: an em-dash in a
    report title/subtitle crashed PDF generation outright
    (FPDFUnicodeEncodingException) because the base Helvetica font only
    supports Latin-1. This confirms the sanitization actually prevents
    that crash.
    """
    unit_id = _make_unit(headers)
    resp = client.get("/api/v1/finance/reports/export", headers=headers, params={
        "report": "apartment_pnl", "format": "pdf", "unit_id": unit_id, "period_year": 2026, "period_month": 7,
    })
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"


def test_unauthenticated_cannot_export_reports():
    resp = client.get("/api/v1/finance/reports/export", params={"report": "cashbook", "format": "pdf"})
    assert resp.status_code == 401
