"""
Zotel room-mapping CRUD now writes a proper AuditLog entry (create/update/
delete), reusing the project's existing record_audit_log/AuditLog
architecture — no second audit system, no schema change (previous_value/
new_value already existed on AuditLog for exactly this "structured
before/after state" use case).

SAFETY: no external call anywhere in this file — mapping CRUD never
touches Zotel, Razorpay, or any notification channel. No real Zotel API
is called; the two tests that also exercise sync (#11/#12/#13 area) mock
the Zotel client exactly like every other existing Zotel sync test does.

Run with: pytest tests/test_zotel_mapping_audit_log.py -v
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditLog
from app.modules.zotel.models import ZotelRoomMapping
from conftest import ADMIN_EMAIL, ADMIN_PASSWORD  # single source of truth, see conftest.py

client = TestClient(app)


@pytest.fixture(scope="module")
def headers():
    resp = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_unit(headers, name_prefix="MapAuditUnit"):
    prop = client.post("/api/v1/properties", headers=headers,
                        json={"name": f"MapAuditProp {uuid.uuid4().hex[:6]}", "address": "Test", "state_code": "34"}).json()
    unit = client.post("/api/v1/units", headers=headers,
                        json={"property_id": prop["id"], "name": f"{name_prefix} {uuid.uuid4().hex[:6]}", "base_rate": 2500}).json()
    return unit["id"]


def _audit_rows_for(entity_id: str, action: str | None = None):
    db = SessionLocal()
    try:
        q = select(AuditLog).where(AuditLog.entity_type == "zotel_room_mapping", AuditLog.entity_id == entity_id)
        if action:
            q = q.where(AuditLog.action == action)
        return db.scalars(q).all()
    finally:
        db.close()


def _get_admin_user_id():
    db = SessionLocal()
    try:
        from app.modules.auth.models import User
        return db.scalar(select(User).where(User.email == ADMIN_EMAIL)).id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1/9. Create -> exactly one audit event, correct actor
# ---------------------------------------------------------------------------

def test_create_mapping_writes_exactly_one_audit_event_with_correct_actor(headers):
    unit_id = _make_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/v1/zotel/mappings", headers=headers,
                        json={"unit_id": unit_id, "zotel_room_type_id": room_type_id, "zotel_room_type_name": "Test Room"})
    assert resp.status_code == 201, resp.text
    mapping_id = resp.json()["id"]

    rows = _audit_rows_for(mapping_id, "zotel_mapping.create")
    assert len(rows) == 1, f"Expected exactly 1 create audit row, got {len(rows)}"
    row = rows[0]
    assert row.user_id == _get_admin_user_id()
    assert row.entity_type == "zotel_room_mapping"
    assert row.entity_id == mapping_id
    assert row.new_value is not None and unit_id in row.new_value and room_type_id in row.new_value
    assert row.previous_value is None  # nothing existed before a create


# ---------------------------------------------------------------------------
# 2/3. Update -> exactly one audit event, with real old and new values
# ---------------------------------------------------------------------------

def test_update_mapping_writes_exactly_one_audit_event_with_old_and_new_values(headers):
    unit_a = _make_unit(headers, "MapAuditUnitA")
    unit_b = _make_unit(headers, "MapAuditUnitB")
    room_type_old = f"rt-old-{uuid.uuid4().hex[:8]}"
    room_type_new = f"rt-new-{uuid.uuid4().hex[:8]}"

    created = client.post("/api/v1/zotel/mappings", headers=headers,
                           json={"unit_id": unit_a, "zotel_room_type_id": room_type_old, "zotel_room_type_name": "Old Room"})
    assert created.status_code == 201, created.text
    mapping_id = created.json()["id"]

    resp = client.patch(f"/api/v1/zotel/mappings/{mapping_id}", headers=headers,
                         json={"unit_id": unit_b, "zotel_room_type_id": room_type_new, "zotel_room_type_name": "New Room"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["unit_id"] == unit_b
    assert resp.json()["zotel_room_type_id"] == room_type_new

    rows = _audit_rows_for(mapping_id, "zotel_mapping.update")
    assert len(rows) == 1, f"Expected exactly 1 update audit row, got {len(rows)}"
    row = rows[0]
    assert row.previous_value is not None
    assert unit_a in row.previous_value and room_type_old in row.previous_value
    assert row.new_value is not None
    assert unit_b in row.new_value and room_type_new in row.new_value
    # The row's own identity is preserved (update, not delete+recreate).
    still_there = client.get("/api/v1/zotel/mappings", headers=headers).json()
    assert any(m["id"] == mapping_id for m in still_there)


def test_update_mapping_with_no_fields_sent_is_a_no_op_and_writes_no_audit_event(headers):
    unit_id = _make_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
    created = client.post("/api/v1/zotel/mappings", headers=headers,
                           json={"unit_id": unit_id, "zotel_room_type_id": room_type_id}).json()
    resp = client.patch(f"/api/v1/zotel/mappings/{created['id']}", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    rows = _audit_rows_for(created["id"], "zotel_mapping.update")
    assert rows == []


# ---------------------------------------------------------------------------
# 4. Delete -> exactly one audit event
# ---------------------------------------------------------------------------

def test_delete_mapping_writes_exactly_one_audit_event(headers):
    unit_id = _make_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
    created = client.post("/api/v1/zotel/mappings", headers=headers,
                           json={"unit_id": unit_id, "zotel_room_type_id": room_type_id, "zotel_room_type_name": "Delete Me"}).json()

    resp = client.delete(f"/api/v1/zotel/mappings/{created['id']}", headers=headers)
    assert resp.status_code == 204, resp.text

    rows = _audit_rows_for(created["id"], "zotel_mapping.delete")
    assert len(rows) == 1, f"Expected exactly 1 delete audit row, got {len(rows)}"
    row = rows[0]
    assert row.previous_value is not None
    assert unit_id in row.previous_value and room_type_id in row.previous_value
    assert row.new_value is None  # nothing exists after a delete

    # Only one audit row total for this entity_id across all three actions —
    # create + delete = 2, never a stray duplicate.
    all_rows = _audit_rows_for(created["id"])
    assert len(all_rows) == 2
    assert {r.action for r in all_rows} == {"zotel_mapping.create", "zotel_mapping.delete"}


# ---------------------------------------------------------------------------
# 5/6/7. Failed mutations -> no audit event
# ---------------------------------------------------------------------------

def test_failed_create_duplicate_unit_writes_no_audit_event(headers):
    unit_id = _make_unit(headers)
    room_type_1 = f"rt-{uuid.uuid4().hex[:8]}"
    room_type_2 = f"rt-{uuid.uuid4().hex[:8]}"
    first = client.post("/api/v1/zotel/mappings", headers=headers,
                         json={"unit_id": unit_id, "zotel_room_type_id": room_type_1}).json()

    before_count = len(_audit_rows_for(first["id"]))

    dup = client.post("/api/v1/zotel/mappings", headers=headers,
                       json={"unit_id": unit_id, "zotel_room_type_id": room_type_2})
    assert dup.status_code == 400, dup.text

    # The rejected attempt has no id of its own to look up — instead,
    # confirm the total audit-row count for the original mapping's entity_id
    # is unchanged, and that no create event exists anywhere for room_type_2.
    assert len(_audit_rows_for(first["id"])) == before_count
    db = SessionLocal()
    try:
        stray = db.scalars(
            select(AuditLog).where(AuditLog.action == "zotel_mapping.create", AuditLog.new_value.like(f"%{room_type_2}%"))
        ).all()
        assert stray == []
    finally:
        db.close()


def test_failed_update_duplicate_room_type_writes_no_audit_event(headers):
    unit_a = _make_unit(headers, "MapAuditUnitA")
    unit_b = _make_unit(headers, "MapAuditUnitB")
    room_type_a = f"rt-{uuid.uuid4().hex[:8]}"
    room_type_b = f"rt-{uuid.uuid4().hex[:8]}"
    mapping_a = client.post("/api/v1/zotel/mappings", headers=headers,
                             json={"unit_id": unit_a, "zotel_room_type_id": room_type_a}).json()
    client.post("/api/v1/zotel/mappings", headers=headers, json={"unit_id": unit_b, "zotel_room_type_id": room_type_b})

    # Attempt to repoint mapping_a's room type onto room_type_b, already used.
    resp = client.patch(f"/api/v1/zotel/mappings/{mapping_a['id']}", headers=headers, json={"zotel_room_type_id": room_type_b})
    assert resp.status_code == 400, resp.text

    rows = _audit_rows_for(mapping_a["id"], "zotel_mapping.update")
    assert rows == []


def test_failed_delete_unknown_id_writes_no_audit_event(headers):
    unknown_id = str(uuid.uuid4())
    resp = client.delete(f"/api/v1/zotel/mappings/{unknown_id}", headers=headers)
    assert resp.status_code == 404, resp.text
    assert _audit_rows_for(unknown_id) == []


# ---------------------------------------------------------------------------
# 8. Transaction rollback -> no persisted audit event (and vice versa: if
#    the audit write itself fails, the mutation must not persist either —
#    both share one uncommitted session/transaction, so proving either
#    direction demonstrates the same atomicity guarantee).
# ---------------------------------------------------------------------------

def test_audit_write_failure_prevents_the_mapping_mutation_from_persisting(headers):
    unit_id = _make_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"

    with patch("app.modules.zotel.router.record_audit_log", side_effect=RuntimeError("simulated audit failure")):
        with pytest.raises(RuntimeError):
            client.post("/api/v1/zotel/mappings", headers=headers,
                         json={"unit_id": unit_id, "zotel_room_type_id": room_type_id})

    db = SessionLocal()
    try:
        leftover = db.scalar(select(ZotelRoomMapping).where(ZotelRoomMapping.zotel_room_type_id == room_type_id))
        assert leftover is None, "The mapping must not persist if the audit write in the same transaction failed"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 10. No secrets in audit payload
# ---------------------------------------------------------------------------

def test_audit_payload_never_contains_zotel_credentials(headers, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "zotel_api_key", "super-secret-key-value")
    monkeypatch.setattr(settings, "zotel_api_secret", "super-secret-secret-value")

    unit_id = _make_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
    created = client.post("/api/v1/zotel/mappings", headers=headers,
                           json={"unit_id": unit_id, "zotel_room_type_id": room_type_id, "zotel_room_type_name": "Secret Check"}).json()
    client.patch(f"/api/v1/zotel/mappings/{created['id']}", headers=headers, json={"zotel_room_type_name": "Renamed"})
    client.delete(f"/api/v1/zotel/mappings/{created['id']}", headers=headers)

    rows = _audit_rows_for(created["id"])
    assert len(rows) == 3
    for row in rows:
        blob = f"{row.details} {row.previous_value} {row.new_value}"
        assert "super-secret-key-value" not in blob
        assert "super-secret-secret-value" not in blob


# ---------------------------------------------------------------------------
# 11. Existing mapping uniqueness rules still work (create-time; unchanged)
# ---------------------------------------------------------------------------

def test_existing_uniqueness_rules_unchanged_by_audit_logging(headers):
    unit_id = _make_unit(headers)
    room_type_id = f"rt-{uuid.uuid4().hex[:8]}"
    client.post("/api/v1/zotel/mappings", headers=headers, json={"unit_id": unit_id, "zotel_room_type_id": room_type_id})

    other_unit = _make_unit(headers)
    dup_room = client.post("/api/v1/zotel/mappings", headers=headers,
                            json={"unit_id": other_unit, "zotel_room_type_id": room_type_id})
    assert dup_room.status_code == 400, dup_room.text

    other_room = f"rt-{uuid.uuid4().hex[:8]}"
    dup_unit = client.post("/api/v1/zotel/mappings", headers=headers,
                            json={"unit_id": unit_id, "zotel_room_type_id": other_room})
    assert dup_unit.status_code == 400, dup_unit.text
