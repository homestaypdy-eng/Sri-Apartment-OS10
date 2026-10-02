"""Customer-scoped identity verification over the public account boundary.

All writes in this module run only under the database safety gate in
conftest.py (TEST_DATABASE_URL must resolve to the isolated test database).
"""
import io
import uuid
from datetime import datetime, timezone
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.bookings.models import Booking, Guest, GuestDocument
from app.modules.customer_auth.models import CustomerAccount
from app.modules.properties.models import Property, Unit
from app.modules.public_booking.models import BookingHold

client = TestClient(app)
_REAL_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 40
_REAL_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\n%%EOF"


@pytest.fixture(autouse=True)
def _clear_customer_auth_rate_limits():
    from app.shared import rate_limit
    rate_limit._attempts.clear()
    yield
    rate_limit._attempts.clear()


def _register_and_login(label: str = "Customer") -> tuple[dict, dict]:
    email = f"{label.lower()}-{uuid.uuid4().hex[:10]}@example.com"
    phone = f"9{uuid.uuid4().int % 10**9:09d}"
    registration = client.post("/api/v1/customer-auth/register", json={
        "first_name": label, "last_name": "Identity", "email": email,
        "phone": phone, "password": "CorrectHorse123!",
        "terms_accepted": True, "privacy_accepted": True,
    })
    assert registration.status_code == 201, registration.text
    start = client.post("/api/v1/customer-auth/login", json={
        "channel": "email", "identifier": email, "password": "CorrectHorse123!",
    })
    assert start.status_code == 200, start.text
    verified = client.post("/api/v1/customer-auth/login/verify-otp", json={
        "challenge_id": start.json()["challenge_id"], "code": start.json()["dev_otp_code"],
    })
    assert verified.status_code == 200, verified.text
    return registration.json(), {"Authorization": f"Bearer {verified.json()['access_token']}"}


def _identity_profile(headers: dict):
    return client.post("/api/v1/customer-auth/me/identity/profile", headers=headers)


def _upload(headers: dict, *, data: bytes = _REAL_JPEG, content_type: str = "image/jpeg", document_type: str = "passport", params=None):
    return client.post(
        "/api/v1/customer-auth/me/identity/documents", headers=headers,
        params={"document_type": document_type, **(params or {})},
        files={"file": ("identity.jpg", io.BytesIO(data), content_type)},
    )


@pytest.fixture
def isolated_document_storage(tmp_path, monkeypatch):
    from app.modules.bookings import document_storage
    monkeypatch.setattr(document_storage, "STORAGE_ROOT", tmp_path / "identity-documents")
    monkeypatch.setattr(document_storage, "STORAGE_BACKEND", "local")


def test_customer_identity_status_resolves_only_its_server_linked_guest():
    customer, headers = _register_and_login("Own")
    initialized = _identity_profile(headers)
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["status"] == "not_required"

    own_status = client.get("/api/v1/customer-auth/me/identity", headers=headers)
    assert own_status.status_code == 200, own_status.text
    assert own_status.json()["status"] == "not_required"
    assert "guest_id" not in own_status.json()

    other, other_headers = _register_and_login("Other")
    _identity_profile(other_headers)
    assert other["id"] != customer["id"]
    assert client.get("/api/v1/customer-auth/me/identity", headers=headers).json() == own_status.json()


def test_email_and_phone_otp_verification_is_recorded_on_same_customer_account():
    customer, _ = _register_and_login("VerifiedChannels")
    account_id = uuid.UUID(customer["id"])
    with SessionLocal() as db:
        account = db.get(CustomerAccount, account_id)
        assert account.email_verified_at is not None
        assert account.phone_verified_at is None

    phone_start = client.post("/api/v1/customer-auth/login", json={
        "channel": "phone", "identifier": customer["phone"], "password": "CorrectHorse123!",
    })
    assert phone_start.status_code == 200, phone_start.text
    phone_verified = client.post("/api/v1/customer-auth/login/verify-otp", json={
        "challenge_id": phone_start.json()["challenge_id"],
        "code": phone_start.json()["dev_otp_code"],
    })
    assert phone_verified.status_code == 200, phone_verified.text
    assert phone_verified.json()["customer"]["id"] == customer["id"]

    with SessionLocal() as db:
        account = db.get(CustomerAccount, account_id)
        assert account.email_verified_at is not None
        assert account.phone_verified_at is not None
    identity = _identity_profile({"Authorization": f"Bearer {phone_verified.json()['access_token']}"})
    assert identity.status_code == 200, identity.text


def test_verified_email_links_one_existing_guest_without_name_matching():
    customer, headers = _register_and_login("ExactContact")
    with SessionLocal() as db:
        guest = Guest(
            name="A Different Display Name", phone=f"8{uuid.uuid4().int % 10**9:09d}",
            email=customer["email"], identity_status="verified",
        )
        db.add(guest)
        db.commit()
        guest_id = guest.id

    response = _identity_profile(headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "verified"
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        assert account.guest_id == guest_id
        assert db.scalar(select(Guest.id).where(Guest.email == customer["email"])) == guest_id


def test_customer_upload_and_submit_are_scoped_to_own_guest_even_with_guest_id_param(isolated_document_storage):
    _, headers_a = _register_and_login("UploadA")
    _, headers_b = _register_and_login("UploadB")
    assert _identity_profile(headers_a).status_code == 200
    assert _identity_profile(headers_b).status_code == 200
    upload_b = _upload(headers_b)
    assert upload_b.status_code == 201, upload_b.text

    with SessionLocal() as db:
        guest_b = db.scalar(select(CustomerAccount).where(CustomerAccount.email.like("uploadb-%")))
        assert guest_b is not None and guest_b.guest_id is not None
        doc_b = db.get(GuestDocument, uuid.UUID(upload_b.json()["id"]))
        assert doc_b.guest_id == guest_b.guest_id

    denied_download = client.get(
        f"/api/v1/guests/{guest_b.guest_id}/documents/{upload_b.json()['id']}/download",
        headers=headers_a,
    )
    assert denied_download.status_code in (401, 403)
    assert client.get(
        f"/api/v1/customer-auth/me/identity/documents/{upload_b.json()['id']}/download",
        headers=headers_a,
    ).status_code == 404

    own_upload = _upload(headers_a, params={"guest_id": str(guest_b.guest_id)})
    assert own_upload.status_code == 201, own_upload.text
    with SessionLocal() as db:
        own_doc = db.get(GuestDocument, uuid.UUID(own_upload.json()["id"]))
        account_a = db.scalar(select(CustomerAccount).where(CustomerAccount.email.like("uploada-%")))
        assert account_a is not None
        assert own_doc.guest_id == account_a.guest_id
        assert own_doc.guest_id != guest_b.guest_id

    cross_submit = client.post("/api/v1/customer-auth/me/identity/submit", headers=headers_a, json={"document_id": upload_b.json()["id"]})
    assert cross_submit.status_code == 404
    own_submit = client.post("/api/v1/customer-auth/me/identity/submit", headers=headers_a, json={
        "document_id": own_upload.json()["id"], "identity_status": "verified",
        "identity_verified_by": str(uuid.uuid4()), "identity_verified_at": datetime.now(timezone.utc).isoformat(),
    })
    assert own_submit.status_code == 200, own_submit.text
    assert own_submit.json()["status"] == "submitted"
    with SessionLocal() as db:
        account_a = db.scalar(select(CustomerAccount).where(CustomerAccount.email.like("uploada-%")))
        guest_a = db.get(Guest, account_a.guest_id)
        assert guest_a.identity_status == "submitted"
        assert guest_a.identity_verified_by is None
        assert guest_a.identity_verified_at is None


def test_customer_token_cannot_read_another_identity_or_call_staff_verify_reject(isolated_document_storage):
    _, headers_a = _register_and_login("PrivateA")
    _, headers_b = _register_and_login("PrivateB")
    _identity_profile(headers_a)
    _identity_profile(headers_b)
    with SessionLocal() as db:
        account_b = db.scalar(select(CustomerAccount).where(CustomerAccount.email.like("privateb-%")))
        guest_b_id = account_b.guest_id
        guest_b = db.get(Guest, guest_b_id)
        guest_b.identity_status = "verified"
        db.commit()
    upload_b = _upload(headers_b)
    assert upload_b.status_code == 201, upload_b.text
    # Supplying another guest_id to the customer "me" route does not
    # change the target; the distinct state proves only account A's own
    # linked profile was read.
    own_status = client.get("/api/v1/customer-auth/me/identity", headers=headers_a, params={"guest_id": str(guest_b_id)})
    assert own_status.status_code == 200 and own_status.json()["status"] == "not_required"
    assert client.get(f"/api/v1/guests/{guest_b_id}/documents", headers=headers_a).status_code in (401, 403)
    assert client.get(
        f"/api/v1/guests/{guest_b_id}/documents/{upload_b.json()['id']}/download", headers=headers_a,
    ).status_code in (401, 403)
    assert client.get(
        f"/api/v1/customer-auth/me/identity/documents/{upload_b.json()['id']}/download", headers=headers_a,
    ).status_code == 404
    for action in ("verify", "reject"):
        resp = client.post(f"/api/v1/guests/{guest_b_id}/identity/{action}", headers=headers_a, json={"document_id": str(uuid.uuid4()), "reason": "test"})
        assert resp.status_code in (401, 403)
    assert client.get(f"/api/v1/customer-auth/me/identity/{guest_b_id}", headers=headers_a).status_code == 404


def test_ambiguous_verified_guest_matches_are_rejected_without_linking():
    customer, headers = _register_and_login("Ambiguous")
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        email_guest = Guest(name="Email Match", phone=f"8{uuid.uuid4().int % 10**9:09d}", email=account.email)
        phone_digits = "".join(ch for ch in account.phone if ch.isdigit())[-10:]
        phone_guest = Guest(name="Phone Match", phone=phone_digits, email=None)
        db.add_all([email_guest, phone_guest])
        db.commit()
        assert account.email_verified_at is not None
        account.phone_verified_at = datetime.now(timezone.utc)
        db.commit()

    response = _identity_profile(headers)
    assert response.status_code == 409
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        assert account.guest_id is None


def test_unverified_contact_collision_is_not_used_to_link_or_create_duplicate_guest():
    customer, headers = _register_and_login("UnverifiedCollision")
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        existing_phone_guest = Guest(
            name="Phone Owner", phone="".join(ch for ch in account.phone if ch.isdigit())[-10:],
        )
        db.add(existing_phone_guest)
        db.commit()

    response = _identity_profile(headers)
    assert response.status_code == 409
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        assert account.guest_id is None
        matches = db.scalars(select(Guest).where(Guest.phone == "".join(ch for ch in account.phone if ch.isdigit())[-10:])).all()
        assert len(matches) == 1


def test_verified_guest_link_is_stable_and_additional_guests_remain_unverified():
    customer, headers = _register_and_login("Reuse")
    first = _identity_profile(headers)
    assert first.status_code == 200
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        guest = db.get(Guest, account.guest_id)
        guest.identity_status = "verified"
        db.commit()
        first_guest_id = account.guest_id
    second = _identity_profile(headers)
    assert second.status_code == 200
    assert second.json()["status"] == "verified"
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        assert account.guest_id == first_guest_id
        additional = Guest(name="Additional", phone=f"7{uuid.uuid4().int % 10**9:09d}")
        db.add(additional)
        db.commit()
        assert additional.identity_status == "not_required"


def test_future_public_booking_reuses_the_linked_verified_main_guest():
    from app.modules.portal.auth_service import normalize_phone
    from app.modules.public_booking.router import _convert_hold_to_booking

    customer, headers = _register_and_login("FutureBooking")
    assert _identity_profile(headers).status_code == 200
    with SessionLocal() as db:
        account = db.get(CustomerAccount, uuid.UUID(customer["id"]))
        guest = db.get(Guest, account.guest_id)
        guest.identity_status = "verified"
        phone = normalize_phone(account.phone)
        property_ = Property(name=f"CustomerIdentity-{uuid.uuid4().hex[:8]}", address="Test", state_code="34")
        db.add(property_)
        db.flush()
        unit = Unit(property_id=property_.id, name=f"Identity-{uuid.uuid4().hex[:8]}", base_rate=2000)
        db.add(unit)
        db.flush()
        check_in = date.today() + timedelta(days=4000)
        hold = BookingHold(
            unit_id=unit.id, guest_name="Future Booking", guest_phone=phone, guest_email=account.email,
            check_in=check_in, check_out=check_in + timedelta(days=2), nights=2,
            subtotal=4000, discount_amount=0, cgst=100, sgst=100, igst=0,
            gst_rate_percent=5, total=4200, advance_amount=4200,
            status="paid", razorpay_payment_id=f"test-{uuid.uuid4().hex}",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        db.add(hold)
        db.flush()
        booking, _invoice = _convert_hold_to_booking(db, hold)
        db.commit()
        assert booking.guest_id == guest.id
        assert db.get(Guest, booking.guest_id).identity_status == "verified"
        db.refresh(account)
        assert account.guest_id == booking.guest_id


@pytest.mark.parametrize(("data", "content_type", "expected"), [
    (b"<html>not an image</html>", "image/jpeg", 415),
    (_REAL_JPEG, "text/html", 415),
    (b"", "image/jpeg", 400),
])
def test_customer_identity_upload_rejects_invalid_file_content(data, content_type, expected, isolated_document_storage):
    _, headers = _register_and_login("InvalidFile")
    assert _identity_profile(headers).status_code == 200
    response = _upload(headers, data=data, content_type=content_type)
    assert response.status_code == expected, response.text


def test_customer_can_upload_valid_pdf_to_own_private_identity_profile(isolated_document_storage):
    _, headers = _register_and_login("PdfUpload")
    assert _identity_profile(headers).status_code == 200
    response = client.post(
        "/api/v1/customer-auth/me/identity/documents", headers=headers,
        params={"document_type": "passport"},
        files={"file": ("passport.pdf", io.BytesIO(_REAL_PDF), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        account = db.scalar(select(CustomerAccount).where(CustomerAccount.email.like("pdfupload-%")))
        document = db.get(GuestDocument, uuid.UUID(response.json()["id"]))
        assert account is not None and document is not None
        assert document.guest_id == account.guest_id
        assert document.content_type == "application/pdf"
        assert document.storage_key.endswith(".pdf")
        assert document.storage_backend == "local"


def test_customer_identity_upload_rejects_files_larger_than_ten_mb(isolated_document_storage):
    _, headers = _register_and_login("LargeFile")
    assert _identity_profile(headers).status_code == 200
    response = _upload(headers, data=_REAL_JPEG + b"x" * (10 * 1024 * 1024), content_type="image/jpeg")
    assert response.status_code == 413, response.text
