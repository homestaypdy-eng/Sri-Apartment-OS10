"""Local-only integration coverage for GuestDocument storage coexistence."""
import hashlib
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.modules.bookings import document_storage
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD

client = TestClient(app)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"x" * 40


def test_production_storage_fails_closed_for_ephemeral_or_unconfigured_backends(tmp_path):
    validate = document_storage.validate_production_storage_config
    with pytest.raises(RuntimeError, match="durable storage"):
        validate(environment="production", backend="local", root="/tmp/id-docs")
    with pytest.raises(RuntimeError, match="temporary directory"):
        validate(environment="production", backend="railway_volume", root="/tmp/id-docs")
    with pytest.raises(RuntimeError, match="S3-compatible"):
        validate(environment="production", backend="s3", bucket="", region="")
    validate(environment="production", backend="s3", bucket="private-docs", region="ap-south-1")
    validate(environment="production", backend="railway_volume", root="/data/guest-documents")


def test_nonproduction_can_keep_local_document_storage(tmp_path):
    document_storage.validate_production_storage_config(environment="development", backend="local", root=str(tmp_path))
    document_storage.validate_production_storage_config(environment="test", backend="local", root="/tmp/id-docs")


@pytest.fixture(scope="module")
def headers():
    response = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(autouse=True)
def private_temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(document_storage, "STORAGE_ROOT", tmp_path / "guest-documents")
    monkeypatch.setattr(document_storage, "STORAGE_BACKEND", "local")


def _guest(headers):
    response = client.post("/api/v1/guests", headers=headers,
                           json={"name": "Storage Test Guest", "phone": f"98{uuid.uuid4().int % 10**8:08d}"})
    assert response.status_code == 201, response.text
    return response.json()


def _upload(headers, guest, name="client-controlled.html", data=JPEG, content_type="image/jpeg"):
    return client.post(
        f"/api/v1/guests/{guest['id']}/documents", headers=headers,
        params={"document_type": "passport", "side": "front"},
        files={"file": (name, io.BytesIO(data), content_type)},
    )


@pytest.mark.parametrize(("name", "content_type", "data", "extension"), [
    ("scan.jpg", "image/jpeg", JPEG, ".jpg"),
    ("scan.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"x" * 20, ".png"),
    ("scan.webp", "image/webp", b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"x" * 20, ".webp"),
    ("scan.pdf", "application/pdf", b"%PDF-1.4\n" + b"x" * 20, ".pdf"),
])
def test_all_allowed_formats_store_with_validated_extension(headers, name, content_type, data, extension):
    guest = _guest(headers)
    response = _upload(headers, guest, name=name, data=data, content_type=content_type)
    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        stored = db.get(GuestDocument, uuid.UUID(response.json()["id"]))
        assert stored.storage_key.endswith(extension)
        assert stored.file_size_bytes == len(data)
        assert stored.sha256_checksum == hashlib.sha256(data).hexdigest()


def test_client_filename_cannot_control_storage_path_or_download_header(headers):
    guest = _guest(headers)
    response = _upload(headers, guest, name="../../../../outside.jpg")
    assert response.status_code == 201, response.text
    doc_id = response.json()["id"]
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        stored = db.get(GuestDocument, uuid.UUID(doc_id))
        assert "/" not in stored.storage_key and ".." not in stored.storage_key
    downloaded = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc_id}/download", headers=headers)
    assert downloaded.status_code == 200
    assert ".." not in downloaded.headers["content-disposition"]


def test_railway_volume_backend_stores_downloads_and_deletes_correctly(headers, monkeypatch):
    """railway_volume shares _root_for_backend's filesystem branch with
    local (a Railway Volume is just a mounted directory) — this proves
    that explicitly, rather than only relying on the local-backend tests
    to exercise it by coincidence of an identical code path."""
    monkeypatch.setattr(document_storage, "STORAGE_BACKEND", "railway_volume")
    guest = _guest(headers)
    response = _upload(headers, guest)
    assert response.status_code == 201, response.text
    doc_id = uuid.UUID(response.json()["id"])
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        doc = db.get(GuestDocument, doc_id)
        assert doc.storage_backend == "railway_volume"
        key = doc.storage_key
    assert (document_storage.STORAGE_ROOT / key).is_file()
    download = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc_id}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == JPEG
    deleted = client.delete(f"/api/v1/guests/{guest['id']}/documents/{doc_id}", headers=headers)
    assert deleted.status_code == 204
    assert not (document_storage.STORAGE_ROOT / key).exists()


def test_upload_records_server_key_size_checksum_and_safe_download(headers):
    guest = _guest(headers)
    response = _upload(headers, guest)
    assert response.status_code == 201, response.text
    doc_id = uuid.UUID(response.json()["id"])
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        doc = db.get(GuestDocument, doc_id)
        assert doc.storage_backend == "local"
        assert doc.storage_key and doc.storage_key != "client-controlled.html"
        assert doc.storage_key.endswith(".jpg")
        assert doc.file_size_bytes == len(JPEG)
        assert doc.sha256_checksum == hashlib.sha256(JPEG).hexdigest()
        assert doc.file_path  # populated for old readers, but not used to resolve new rows
        key = doc.storage_key
    download = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc_id}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == JPEG
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert (document_storage.STORAGE_ROOT / key).is_file()
    other_guest = _guest(headers)
    mismatched_owner = client.get(
        f"/api/v1/guests/{other_guest['id']}/documents/{doc_id}/download", headers=headers,
    )
    assert mismatched_owner.status_code == 404


def test_legacy_file_path_and_new_record_coexist(headers, tmp_path):
    guest = _guest(headers)
    legacy_file = document_storage.STORAGE_ROOT / "legacy.jpg"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_bytes(JPEG)
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        legacy = GuestDocument(guest_id=uuid.UUID(guest["id"]), document_type="passport",
                               filename="legacy.jpg", content_type="image/jpeg",
                               file_path=str(legacy_file), side="front")
        db.add(legacy)
        db.commit()
        db.refresh(legacy)
        legacy_id = legacy.id
        assert legacy.storage_backend is None and legacy.storage_key is None
    new = _upload(headers, guest)
    assert new.status_code == 201, new.text
    legacy_download = client.get(f"/api/v1/guests/{guest['id']}/documents/{legacy_id}/download", headers=headers)
    assert legacy_download.status_code == 200 and legacy_download.content == JPEG
    new_download = client.get(f"/api/v1/guests/{guest['id']}/documents/{new.json()['id']}/download", headers=headers)
    assert new_download.status_code == 200 and new_download.content == JPEG


def test_missing_new_and_legacy_files_return_clean_404(headers, tmp_path):
    guest = _guest(headers)
    new = _upload(headers, guest).json()
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        doc = db.get(GuestDocument, uuid.UUID(new["id"]))
        (document_storage.STORAGE_ROOT / doc.storage_key).unlink()
        legacy = GuestDocument(guest_id=uuid.UUID(guest["id"]), document_type="passport",
                               filename="gone.jpg", content_type="image/jpeg",
                               file_path=str(document_storage.STORAGE_ROOT / "gone.jpg"), side="front")
        db.add(legacy)
        db.commit()
        db.refresh(legacy)
        legacy_id = legacy.id
    for doc_id in (new["id"], str(legacy_id)):
        response = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc_id}/download", headers=headers)
        assert response.status_code == 404
        assert "tmp" not in response.text


def test_delete_removes_new_file_row_and_emits_audit_events(headers):
    guest = _guest(headers)
    uploaded = _upload(headers, guest).json()
    doc_id = uuid.UUID(uploaded["id"])
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        doc = db.get(GuestDocument, doc_id)
        path = document_storage.STORAGE_ROOT / doc.storage_key
        assert path.is_file()
    downloaded = client.get(f"/api/v1/guests/{guest['id']}/documents/{doc_id}/download", headers=headers)
    assert downloaded.status_code == 200
    deleted = client.delete(f"/api/v1/guests/{guest['id']}/documents/{doc_id}", headers=headers)
    assert deleted.status_code == 204
    assert not path.exists()
    with SessionLocal() as db:
        from app.modules.audit.models import AuditLog
        from app.modules.bookings.models import GuestDocument
        from sqlalchemy import select
        assert db.get(GuestDocument, doc_id) is None
        actions = set(db.scalars(select(AuditLog.action).where(
            AuditLog.entity_id == guest["id"],
            AuditLog.action.in_(["guest.document_upload", "guest.document_download", "guest.document_delete"]),
        )).all())
        assert actions == {"guest.document_upload", "guest.document_download", "guest.document_delete"}


def test_rbac_signature_and_size_limit_are_enforced(headers):
    guest = _guest(headers)
    assert client.get(f"/api/v1/guests/{guest['id']}/documents").status_code == 401
    assert _upload(headers, guest, data=b"<html>not an image</html>").status_code == 415
    too_large = _upload(headers, guest, name="large.jpg", data=JPEG + b"x" * (10 * 1024 * 1024))
    assert too_large.status_code == 413


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_parameters = []

    def put_object(self, **kwargs):
        self.put_parameters.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise FakeS3Error(404)
        return {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise FakeS3Error(404)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class FakeS3Error(Exception):
    def __init__(self, status):
        super().__init__("mock S3 error")
        self.response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": status}}


def _use_mock_s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(document_storage, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(document_storage, "S3_BUCKET", "private-test-bucket")
    monkeypatch.setattr(document_storage, "S3_REGION", "test-region-1")
    monkeypatch.setattr(document_storage, "S3_ENDPOINT_URL", "https://objects.test.invalid")
    monkeypatch.setattr(document_storage, "S3_SERVER_SIDE_ENCRYPTION", "AES256")
    monkeypatch.setattr(document_storage, "_s3_client", lambda: fake)
    return fake


def test_s3_adapter_is_private_metadata_only_and_downloads_through_backend(headers, monkeypatch):
    fake = _use_mock_s3(monkeypatch)
    guest = _guest(headers)
    response = _upload(headers, guest)
    assert response.status_code == 201, response.text
    assert not any("url" in key.lower() or "storage_key" in key or "storage_backend" in key for key in response.json())
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        stored = db.get(GuestDocument, uuid.UUID(response.json()["id"]))
        assert stored.storage_backend == "s3"
        assert stored.storage_key and stored.file_path == ""
        key = stored.storage_key
    put = fake.put_parameters[0]
    assert put["Bucket"] == "private-test-bucket"
    assert put["ContentType"] == "image/jpeg"
    assert put["ServerSideEncryption"] == "AES256"
    assert "ACL" not in put  # bucket policy owns access; never grant public ACLs
    downloaded = client.get(f"/api/v1/guests/{guest['id']}/documents/{response.json()['id']}/download", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == JPEG
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    fake.objects.pop(("private-test-bucket", key))
    missing = client.get(f"/api/v1/guests/{guest['id']}/documents/{response.json()['id']}/download", headers=headers)
    assert missing.status_code == 404
    assert "bucket" not in missing.text.lower() and "endpoint" not in missing.text.lower()
    # Switching the write backend must not pretend old files were migrated;
    # the existing local file_path remains readable when it is still present.
    legacy_path = document_storage.STORAGE_ROOT / "legacy-before-s3.jpg"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(JPEG)
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        legacy = GuestDocument(guest_id=uuid.UUID(guest["id"]), document_type="passport",
                               filename="legacy-before-s3.jpg", content_type="image/jpeg",
                               file_path=str(legacy_path), side="front")
        db.add(legacy)
        db.commit()
        db.refresh(legacy)
        legacy_id = legacy.id
    legacy_download = client.get(f"/api/v1/guests/{guest['id']}/documents/{legacy_id}/download", headers=headers)
    assert legacy_download.status_code == 200 and legacy_download.content == JPEG
    second = _upload(headers, guest, name="delete-me.jpg")
    assert second.status_code == 201
    with SessionLocal() as db:
        from app.modules.bookings.models import GuestDocument
        second_doc = db.get(GuestDocument, uuid.UUID(second.json()["id"]))
        second_key = second_doc.storage_key
    deleted = client.delete(f"/api/v1/guests/{guest['id']}/documents/{second.json()['id']}", headers=headers)
    assert deleted.status_code == 204
    assert ("private-test-bucket", second_key) not in fake.objects


def test_storage_failure_creates_no_metadata(headers, monkeypatch):
    guest = _guest(headers)
    def fail_store(*args, **kwargs):
        raise document_storage.StorageUnavailable("internal details must not escape")
    monkeypatch.setattr(document_storage, "store_new_document", fail_store)
    response = _upload(headers, guest)
    assert response.status_code == 503
    assert "internal details" not in response.text
    with SessionLocal() as db:
        from sqlalchemy import select
        from app.modules.bookings.models import GuestDocument
        rows = db.scalars(select(GuestDocument).where(GuestDocument.guest_id == uuid.UUID(guest["id"]))).all()
        assert rows == []


def test_database_audit_failure_rolls_back_metadata_and_compensates_s3(headers, monkeypatch):
    fake = _use_mock_s3(monkeypatch)
    guest = _guest(headers)
    from app.modules.bookings import router as bookings_router
    monkeypatch.setattr(bookings_router, "record_audit_log", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")))
    # This app's security_headers_middleware (main.py, @app.middleware("http"))
    # is Starlette BaseHTTPMiddleware under the hood, which wraps an
    # unhandled route exception in an ExceptionGroup as it unwinds —
    # TestClient's default raise_server_exceptions=True then re-raises it
    # into the test rather than handing back a clean 500 response, even
    # though the app's own @app.exception_handler(Exception) would turn it
    # into one for a real client. Expecting the raise (as this project's
    # other tests already do for the same TestClient behavior — see
    # test_zotel_mapping_audit_log.py) is what actually exercises this
    # path; asserting a 500 status code here never would.
    with pytest.raises(RuntimeError, match="audit failed"):
        _upload(headers, guest)
    assert fake.objects == {}
    with SessionLocal() as db:
        from sqlalchemy import select
        from app.modules.bookings.models import GuestDocument
        rows = db.scalars(select(GuestDocument).where(GuestDocument.guest_id == uuid.UUID(guest["id"]))).all()
        assert rows == []
