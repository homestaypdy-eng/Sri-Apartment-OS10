import asyncio
import datetime as dt
import uuid
from collections.abc import AsyncIterator

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.security import TokenError, decode_token
from app.db.session import SessionLocal, engine
from app.modules.auth.models import User

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])
HEARTBEAT_SECONDS = 15
_TOPIC_ROLES = {
    "bookings.changed": {"admin", "manager", "staff"},
    "availability.changed": {"admin", "manager", "staff"},
    "properties.changed": {"admin", "manager", "staff"},
    "guests.changed": {"admin", "manager", "staff"},
    "inquiries.changed": {"admin", "manager", "staff"},
    "payments.changed": {"admin", "manager", "staff"},
    "housekeeping.changed": {"admin", "manager", "staff"},
    "maintenance.changed": {"admin", "manager", "staff"},
    "guest_requests.changed": {"admin", "manager", "staff"},
    "inventory.changed": {"admin", "manager", "staff"},
    "finance.changed": {"admin", "manager", "staff"},
    "staff_operations.changed": {"admin", "manager", "staff"},
    "notifications.changed": {"admin", "manager", "staff"},
    "messages.changed": {"admin", "manager", "staff"},
    "users.changed": {"admin"},
    "audit.changed": {"admin"},
    "integrations.changed": {"admin"},
    "settings.changed": {"admin"},
}


def _access_identity(request: Request) -> tuple[uuid.UUID, str, int]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Authentication required")
    try:
        payload = decode_token(token, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
        expires_at = int(payload["exp"])
        token_role = str(payload.get("role", ""))
    except (TokenError, KeyError, TypeError, ValueError):
        raise HTTPException(401, "Invalid or expired token") from None

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active:
            raise HTTPException(401, "User not found or inactive")
        if user.role.name not in {"admin", "manager", "staff"} or user.role.name != token_role:
            raise HTTPException(401, "Session permissions changed; refresh authentication")
        return user_id, user.role.name, expires_at


async def _event_stream(request: Request, user_id: uuid.UUID, role: str, token_expiry: int) -> AsyncIterator[str]:
    dsn = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection:
            await connection.execute("LISTEN os10_realtime")
            # Every new or recovered connection forces a full authoritative
            # fetch of active views; notifications are only optimizations.
            yield "event: resync\ndata: {}\n\n"
            while not await request.is_disconnected():
                if int(dt.datetime.now(dt.timezone.utc).timestamp()) >= token_expiry:
                    yield "event: auth_expired\ndata: {}\n\n"
                    return

                notifications = connection.notifies(timeout=HEARTBEAT_SECONDS, stop_after=1)
                async for notification in notifications:
                    current = await (await connection.execute(
                        "SELECT u.is_active, r.name FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s",
                        (user_id,),
                    )).fetchone()
                    if current is None or not current[0]:
                        yield "event: auth_revoked\ndata: {}\n\n"
                        return
                    if current[1] != role:
                        yield "event: auth_changed\ndata: {}\n\n"
                        return
                    try:
                        event_id = int(notification.payload)
                    except (TypeError, ValueError):
                        continue
                    row = await (await connection.execute(
                        "SELECT topic FROM realtime_outbox WHERE id = %s", (event_id,)
                    )).fetchone()
                    if row and role in _TOPIC_ROLES.get(row[0], set()):
                        # Topic only: never broadcast row data, identifiers,
                        # document metadata, payment details, or audit data.
                        yield f"id: {event_id}\nevent: invalidate\ndata: {{\"topic\":\"{row[0]}\"}}\n\n"

                current = await (await connection.execute(
                    "SELECT u.is_active, r.name FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s",
                    (user_id,),
                )).fetchone()
                if current is None or not current[0]:
                    yield "event: auth_revoked\ndata: {}\n\n"
                    return
                if current[1] != role:
                    yield "event: auth_changed\ndata: {}\n\n"
                    return
                yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        raise


@router.get("/events")
async def realtime_events(request: Request):
    """Authenticated SSE invalidation stream backed by transactional PG NOTIFY."""
    user_id, role, token_expiry = _access_identity(request)
    return StreamingResponse(
        _event_stream(request, user_id, role, token_expiry),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
