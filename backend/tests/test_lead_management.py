"""
Tests for lead management: lead_status transitions, WhatsApp link generation,
email sending (with SMTP mocked), follow-up reminders, offer expiration, and
the sales funnel report.
Run with: pytest tests/test_lead_management.py -v
"""
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _random_phone() -> str:
    """10-digit numeric phone number, unique per call (uuid hex has letters, unsuitable for phone-format tests)."""
    return f"9{uuid.uuid4().int % 10**9:09d}"


from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_inquiry(headers, **overrides):
    payload = {
        "client_name": "Lead Test Client",
        "client_phone": "9700000000",
        "client_email": "leadtest@example.com",
        "flat_room_label": "Test Room",
        "check_in_date": "2030-01-10",
        "check_out_date": "2030-01-13",
        "rate_per_night": 2000,
        "advance_amount": 0,
    }
    payload.update(overrides)
    resp = client.post("/api/v1/inquiries", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- lead_status basics ---

def test_new_inquiry_starts_as_new_lead(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    assert inquiry["lead_status"] == "new"
    assert inquiry["last_contacted_at"] is None
    assert inquiry["next_follow_up_date"] is None


def test_lead_status_update_validates_value(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    resp = client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"lead_status": "enlightened"})
    assert resp.status_code == 400


def test_lead_status_and_follow_up_fields_update_together(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    resp = client.patch(
        f"/api/v1/inquiries/{inquiry['id']}",
        headers=headers,
        json={"lead_status": "follow_up", "next_follow_up_date": "2030-02-01", "follow_up_notes": "Call back next week"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["lead_status"] == "follow_up"
    assert data["next_follow_up_date"] == "2030-02-01"
    assert data["follow_up_notes"] == "Call back next week"


# --- WhatsApp ---

def test_whatsapp_link_has_correct_country_code_and_content(headers):
    phone = _random_phone()
    inquiry = _make_inquiry(headers, client_phone=phone, client_name="Whatsapp Test")
    resp = client.post(f"/api/v1/inquiries/{inquiry['id']}/send/whatsapp", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["whatsapp_url"].startswith(f"https://wa.me/91{phone}?text=")
    assert "Whatsapp Test" in data["message_preview"]
    assert inquiry["offer_no"] in data["message_preview"]


def test_whatsapp_send_bumps_new_lead_to_contacted(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    assert inquiry["lead_status"] == "new"

    client.post(f"/api/v1/inquiries/{inquiry['id']}/send/whatsapp", headers=headers)

    updated = client.get(f"/api/v1/inquiries/{inquiry['id']}", headers=headers).json()
    assert updated["lead_status"] == "contacted"
    assert updated["last_contacted_at"] is not None
    assert updated["status"] == "sent"  # draft -> sent on first contact


def test_whatsapp_send_does_not_regress_further_along_lead_status(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    client.patch(f"/api/v1/inquiries/{inquiry['id']}", headers=headers, json={"lead_status": "follow_up"})

    client.post(f"/api/v1/inquiries/{inquiry['id']}/send/whatsapp", headers=headers)

    updated = client.get(f"/api/v1/inquiries/{inquiry['id']}", headers=headers).json()
    assert updated["lead_status"] == "follow_up"  # NOT reset to 'contacted'


def test_whatsapp_send_logs_communication(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    client.post(f"/api/v1/inquiries/{inquiry['id']}/send/whatsapp", headers=headers)

    log = client.get(f"/api/v1/inquiries/{inquiry['id']}/communications", headers=headers).json()
    assert len(log) == 1
    assert log[0]["channel"] == "whatsapp"
    assert log[0]["success"] is True


# --- Email (SMTP mocked — this environment has no real SMTP access) ---

def test_email_send_fails_cleanly_when_smtp_not_configured(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    resp = client.post(f"/api/v1/inquiries/{inquiry['id']}/send/email", headers=headers, json={})
    assert resp.status_code == 501
    assert "not configured" in resp.json()["detail"].lower()

    log = client.get(f"/api/v1/inquiries/{inquiry['id']}/communications", headers=headers).json()
    assert log[0]["channel"] == "email"
    assert log[0]["success"] is False


def test_email_send_requires_an_email_address(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone(), client_email=None)
    resp = client.post(f"/api/v1/inquiries/{inquiry['id']}/send/email", headers=headers, json={})
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"].lower()


@patch("smtplib.SMTP")
def test_email_send_succeeds_with_mocked_smtp(mock_smtp_class, headers):
    """
    Proves the email-sending CODE PATH works correctly (constructs the
    message, attaches the PDF, calls send_message) without needing real
    SMTP network access, which this sandbox doesn't have.
    """
    from app.modules.inquiries import service as inquiry_service

    with patch.object(inquiry_service.settings, "smtp_host", "smtp.example.com"), \
         patch.object(inquiry_service.settings, "smtp_user", "test@example.com"), \
         patch.object(inquiry_service.settings, "smtp_password", "fake"), \
         patch.object(inquiry_service.settings, "smtp_use_tls", True):

        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__.return_value = mock_server

        inquiry = _make_inquiry(headers, client_phone=_random_phone(), client_email="realtest@example.com")
        # Inquiry creation now also sends its own internal "new enquiry"
        # notification (see trigger_inquiry_created) — correct, real
        # behavior, but a separate concern from what this test verifies.
        # Reset here so the assertions below cleanly check only the
        # deliberate quotation-email action, not conflate the two.
        mock_server.reset_mock()
        resp = client.post(f"/api/v1/inquiries/{inquiry['id']}/send/email", headers=headers, json={})

        assert resp.status_code == 200, resp.text
        assert resp.json()["sent"] is True
        assert resp.json()["to_email"] == "realtest@example.com"

        # Confirm our code actually called the SMTP send, with TLS + login
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@example.com", "fake")
        mock_server.send_message.assert_called_once()

        sent_msg = mock_server.send_message.call_args[0][0]
        assert "Sri Apartment Homestay" in sent_msg["Subject"] or inquiry["offer_no"] in sent_msg["Subject"]
        assert sent_msg["To"] == "realtest@example.com"
        assert sent_msg.is_multipart()  # has the PDF attachment


# --- Follow-ups due ---

def test_follow_up_due_list_includes_past_due_dates(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    client.patch(
        f"/api/v1/inquiries/{inquiry['id']}",
        headers=headers,
        json={"lead_status": "follow_up", "next_follow_up_date": "2020-01-01"},  # long past
    )
    due = client.get("/api/v1/inquiries/follow-ups/due", headers=headers).json()
    assert any(i["id"] == inquiry["id"] for i in due)


def test_follow_up_due_excludes_future_dates(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    future = (date.today() + timedelta(days=30)).isoformat()
    client.patch(
        f"/api/v1/inquiries/{inquiry['id']}",
        headers=headers,
        json={"lead_status": "follow_up", "next_follow_up_date": future},
    )
    due = client.get("/api/v1/inquiries/follow-ups/due", headers=headers).json()
    assert not any(i["id"] == inquiry["id"] for i in due)


def test_follow_up_due_excludes_converted_leads(headers):
    inquiry = _make_inquiry(headers, client_phone=_random_phone())
    client.patch(
        f"/api/v1/inquiries/{inquiry['id']}",
        headers=headers,
        json={"lead_status": "converted", "next_follow_up_date": "2020-01-01"},
    )
    due = client.get("/api/v1/inquiries/follow-ups/due", headers=headers).json()
    assert not any(i["id"] == inquiry["id"] for i in due)


# --- Expiration ---

def test_is_expired_computed_correctly(headers):
    past_inquiry = _make_inquiry(
        headers,
        client_phone=_random_phone(),
        check_in_date="2020-01-10",
        check_out_date="2020-01-13",
        valid_upto="2020-01-05",
    )
    assert past_inquiry["is_expired"] is True

    future_inquiry = _make_inquiry(headers, client_phone=_random_phone())  # valid_upto defaults to check_in in 2030
    assert future_inquiry["is_expired"] is False


def test_expiring_soon_endpoint_respects_window(headers):
    near_date = (date.today() + timedelta(days=2)).isoformat()
    check_in = (date.today() + timedelta(days=10)).isoformat()
    check_out = (date.today() + timedelta(days=13)).isoformat()
    inquiry = _make_inquiry(
        headers,
        client_phone=_random_phone(),
        check_in_date=check_in,
        check_out_date=check_out,
        valid_upto=near_date,
    )
    expiring = client.get("/api/v1/inquiries/expiring", headers=headers, params={"within_days": 3}).json()
    assert any(i["id"] == inquiry["id"] for i in expiring)

    not_expiring = client.get("/api/v1/inquiries/expiring", headers=headers, params={"within_days": 0}).json()
    assert not any(i["id"] == inquiry["id"] for i in not_expiring)


def test_list_inquiries_filters_by_expired(headers):
    past_inquiry = _make_inquiry(
        headers,
        client_phone=_random_phone(),
        check_in_date="2020-01-10",
        check_out_date="2020-01-13",
        valid_upto="2020-01-05",
    )
    expired_list = client.get("/api/v1/inquiries", headers=headers, params={"expired": True}).json()
    assert any(i["id"] == past_inquiry["id"] for i in expired_list)


# --- Sales funnel ---

def test_sales_funnel_counts_match_created_leads(headers):
    unique_marker = uuid.uuid4().hex[:6]
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    inquiry = _make_inquiry(headers, client_phone=_random_phone(), client_name=f"Funnel {unique_marker}")

    funnel = client.get(
        "/api/v1/reports/sales-funnel", headers=headers, params={"date_from": today, "date_to": tomorrow}
    ).json()

    assert funnel["total_inquiries"] >= 1
    new_stage = next(s for s in funnel["stages"] if s["lead_status"] == "new")
    assert new_stage["count"] >= 1
    assert funnel["conversion_rate_percent"] >= 0


def test_sales_funnel_conversion_rate_reflects_converted_leads(headers):
    from app.db.session import SessionLocal
    from app.modules.properties.models import Property, Unit
    from app.modules.bookings.models import Guest

    db = SessionLocal()
    prop = Property(name=f"FunnelProp {uuid.uuid4().hex[:6]}", address="Test", state_code="34")
    db.add(prop)
    db.flush()
    unit = Unit(property_id=prop.id, name=f"FunnelUnit {uuid.uuid4().hex[:6]}", base_rate=2000)
    db.add(unit)
    db.commit()
    unit_id = str(unit.id)
    db.close()

    inquiry = _make_inquiry(
        headers,
        client_phone=_random_phone(),
        unit_id=unit_id,
        advance_amount=1000,
        check_in_date="2030-06-01",
        check_out_date="2030-06-03",
    )
    convert = client.post(f"/api/v1/inquiries/{inquiry['id']}/convert-to-booking", headers=headers, json={})
    assert convert.status_code == 201
    assert convert.json()["inquiry"]["lead_status"] == "converted"

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    funnel = client.get(
        "/api/v1/reports/sales-funnel", headers=headers, params={"date_from": today, "date_to": tomorrow}
    ).json()
    assert funnel["converted_count"] >= 1
    assert funnel["total_converted_value"] > 0


def test_unauthenticated_cannot_access_lead_management_endpoints():
    assert client.get("/api/v1/inquiries/follow-ups/due").status_code == 401
    assert client.get("/api/v1/inquiries/expiring").status_code == 401
    assert client.get("/api/v1/reports/sales-funnel").status_code == 401
    assert client.post(f"/api/v1/inquiries/{uuid.uuid4()}/send/whatsapp").status_code == 401
