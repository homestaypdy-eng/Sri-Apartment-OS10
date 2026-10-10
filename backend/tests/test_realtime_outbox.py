import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.main import app
from app.modules.auth.models import Role, User
from app.shared.realtime_models import RealtimeOutbox
from app.shared.realtime_router import _TOPIC_ROLES

client = TestClient(app)


def _count_topic(topic: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(RealtimeOutbox).where(RealtimeOutbox.topic == topic)) or 0


def test_business_change_creates_transactional_outbox_event():
    before = _count_topic("users.changed")
    with SessionLocal() as db:
        role = db.scalar(select(Role).where(Role.name == "staff"))
        db.add(User(
            name="Realtime test user",
            email=f"realtime-{uuid.uuid4().hex}@example.com",
            password_hash="test-only-not-a-login-hash",
            role_id=role.id,
        ))
        db.commit()
    assert _count_topic("users.changed") == before + 1


def test_rolled_back_business_change_does_not_leave_outbox_event():
    before = _count_topic("users.changed")
    with SessionLocal() as db:
        role = db.scalar(select(Role).where(Role.name == "staff"))
        db.add(User(
            name="Rolled back realtime user",
            email=f"realtime-rollback-{uuid.uuid4().hex}@example.com",
            password_hash="test-only-not-a-login-hash",
            role_id=role.id,
        ))
        db.flush()
        assert db.scalar(select(func.count()).select_from(RealtimeOutbox).where(RealtimeOutbox.topic == "users.changed")) == before + 1
        db.rollback()
    assert _count_topic("users.changed") == before


def test_realtime_stream_requires_authentication():
    assert client.get("/api/v1/realtime/events").status_code == 401


def test_sensitive_invalidation_topics_are_admin_only():
    assert _TOPIC_ROLES["users.changed"] == {"admin"}
    assert _TOPIC_ROLES["settings.changed"] == {"admin"}
    assert _TOPIC_ROLES["audit.changed"] == {"admin"}
    assert _TOPIC_ROLES["integrations.changed"] == {"admin"}
