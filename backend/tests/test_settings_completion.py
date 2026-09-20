"""
Tests completing the Settings module to full production coverage: logo
and QR code upload, system-info (configuration status), roles-
permissions reference, backup export, and theme/WhatsApp fields.
Run with: pytest tests/test_settings_completion.py -v
"""
import io
import json
import uuid

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


def test_logo_upload_and_persists(headers):
    fake_file = io.BytesIO(b"\xff\xd8\xff\xe0fake logo jpeg")
    resp = client.post("/api/v1/settings/logo", headers=headers, files={"file": ("logo.jpg", fake_file, "image/jpeg")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["logo_path"] is not None

    check = client.get("/api/v1/settings", headers=headers).json()
    assert check["logo_path"] is not None

    # Settings is a shared singleton — an invalid/fake image left here
    # would break every other PDF-generating test in the suite (found
    # exactly this way; see the defensive try/except added around
    # pdf.image() calls). Clean up so this test's fake data doesn't leak
    # into the rest of the suite.
    client.put("/api/v1/settings", headers=headers, json={})
    from app.db.session import SessionLocal
    from app.modules.settings.models import SETTINGS_SINGLETON_ID, BusinessSettings
    db = SessionLocal()
    try:
        s = db.get(BusinessSettings, SETTINGS_SINGLETON_ID)
        s.logo_path = None
        db.commit()
    finally:
        db.close()


def test_qr_code_upload_and_persists(headers):
    fake_file = io.BytesIO(b"\xff\xd8\xff\xe0fake qr jpeg")
    resp = client.post("/api/v1/settings/qr-code", headers=headers, files={"file": ("qr.jpg", fake_file, "image/jpeg")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["qr_code_path"] is not None

    from app.db.session import SessionLocal
    from app.modules.settings.models import SETTINGS_SINGLETON_ID, BusinessSettings
    db = SessionLocal()
    try:
        s = db.get(BusinessSettings, SETTINGS_SINGLETON_ID)
        s.qr_code_path = None
        db.commit()
    finally:
        db.close()


def test_logo_upload_rejects_non_image(headers):
    fake_file = io.BytesIO(b"<script>alert(1)</script>")
    resp = client.post("/api/v1/settings/logo", headers=headers, files={"file": ("evil.html", fake_file, "text/html")})
    assert resp.status_code == 415


def test_logo_upload_requires_admin_role():
    resp = client.post("/api/v1/settings/logo", files={"file": ("logo.jpg", io.BytesIO(b"test"), "image/jpeg")})
    assert resp.status_code == 401


def test_logo_upload_logs_audit(headers):
    fake_file = io.BytesIO(b"\xff\xd8\xff\xe0test")
    client.post("/api/v1/settings/logo", headers=headers, files={"file": ("audit_test.jpg", fake_file, "image/jpeg")})
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "settings.logo_upload"}).json()
    assert len(logs) >= 1

    from app.db.session import SessionLocal
    from app.modules.settings.models import SETTINGS_SINGLETON_ID, BusinessSettings
    db = SessionLocal()
    try:
        s = db.get(BusinessSettings, SETTINGS_SINGLETON_ID)
        s.logo_path = None
        db.commit()
    finally:
        db.close()


def test_system_info_reflects_real_state(headers):
    resp = client.get("/api/v1/settings/system-info", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["database_connected"] is True
    assert "environment" in data
    assert "app_version" in data
    assert isinstance(data["email_configured"], bool)
    assert isinstance(data["sms_configured"], bool)


def test_system_info_never_exposes_credentials(headers):
    resp = client.get("/api/v1/settings/system-info", headers=headers).json()
    serialized = json.dumps(resp)
    assert "password" not in serialized.lower()
    assert "api_key" not in serialized.lower()


def test_system_info_requires_admin_not_just_staff(headers):
    email = f"staffsysinfo{uuid.uuid4().hex[:8]}@test.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Staff SysInfo Test", "email": email, "password": "TestPass123!", "role": "staff"})
    staff_token = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"}).json()["access_token"]
    resp = client.get("/api/v1/settings/system-info", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 403


def test_roles_permissions_reflects_real_role_model(headers):
    # RBAC Phase B documentation fix (finding I-4): this reference now
    # covers all three roles, not just the two that existed when this test
    # was written — see test_rbac_phase_b.py's dedicated roles-permissions
    # section for full content coverage of the new "manager" entry.
    resp = client.get("/api/v1/settings/roles-permissions", headers=headers)
    assert resp.status_code == 200
    roles = {r["role"] for r in resp.json()["roles"]}
    assert roles == {"admin", "staff", "manager"}


def test_roles_permissions_accessible_to_staff_too(headers):
    email = f"staffroles{uuid.uuid4().hex[:8]}@test.com"
    client.post("/api/v1/auth/register", headers=headers, json={"name": "Staff Roles Test", "email": email, "password": "TestPass123!", "role": "staff"})
    staff_token = client.post("/api/v1/auth/login", json={"email": email, "password": "TestPass123!"}).json()["access_token"]
    resp = client.get("/api/v1/settings/roles-permissions", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200


def test_backup_download_produces_valid_json_with_real_data(headers):
    resp = client.get("/api/v1/settings/backup", headers=headers)
    assert resp.status_code == 200, resp.text
    data = json.loads(resp.content)
    assert "tables" in data
    assert "business_settings" in data["tables"]
    assert len(data["tables"]["business_settings"]) >= 1
    assert "exported_at" in data


def test_backup_requires_admin_role():
    resp = client.get("/api/v1/settings/backup")
    assert resp.status_code == 401


def test_backup_download_logs_audit(headers):
    client.get("/api/v1/settings/backup", headers=headers)
    logs = client.get("/api/v1/audit/logs", headers=headers, params={"action": "settings.backup_download"}).json()
    assert len(logs) >= 1


def test_theme_color_updatable(headers):
    resp = client.put("/api/v1/settings", headers=headers, json={"theme_color": "#1e40af"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["theme_color"] == "#1e40af"


def test_whatsapp_business_number_updatable(headers):
    resp = client.put("/api/v1/settings", headers=headers, json={"whatsapp_business_number": "919876543210"})
    assert resp.status_code == 200
    assert resp.json()["whatsapp_business_number"] == "919876543210"


def test_existing_settings_update_still_works(headers):
    resp = client.put("/api/v1/settings", headers=headers, json={"company_name": "Regression Check Homestay"})
    assert resp.status_code == 200
    assert resp.json()["company_name"] == "Regression Check Homestay"
    client.put("/api/v1/settings", headers=headers, json={"company_name": "SRI APARTMENT HOMESTAY"})
