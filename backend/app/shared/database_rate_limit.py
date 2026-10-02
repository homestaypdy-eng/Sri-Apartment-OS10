"""PostgreSQL-backed rate limiting for sensitive anonymous booking routes."""

import hashlib
import ipaddress
import math
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.public_booking.models import PublicRateLimitBucket


def client_ip(request: Request) -> str:
    """Use the socket peer only; forwarded headers are untrusted here."""
    peer = request.client.host if request.client else "unknown"
    try:
        return ipaddress.ip_address(peer).compressed
    except ValueError:
        return "unknown"


def _bucket_key(scope: str, ip: str) -> str:
    # Keep endpoint counters separate and avoid storing the raw IP address.
    return hashlib.sha256(f"public-booking:{scope}:{ip}".encode("utf-8")).hexdigest()


def enforce_rate_limit(
    db: Session,
    request: Request,
    *,
    scope: str,
    max_requests: int,
    window_seconds: int = 60,
    now: int | None = None,
) -> None:
    """Atomically count a fixed-window request in PostgreSQL.

    The counter is committed before endpoint business logic runs, so failed
    availability or payment validation attempts still consume quota. A
    database failure fails closed with a generic 503.
    """
    current_time = int(time.time()) if now is None else now
    window_id = current_time // window_seconds
    key = _bucket_key(scope, client_ip(request))
    current = datetime.fromtimestamp(current_time, tz=timezone.utc)
    statement = insert(PublicRateLimitBucket).values(
        bucket_key=key,
        window_id=window_id,
        request_count=1,
        updated_at=current,
    )
    next_count = case(
        (
            PublicRateLimitBucket.window_id == statement.excluded.window_id,
            case(
                (PublicRateLimitBucket.request_count <= max_requests, PublicRateLimitBucket.request_count + 1),
                else_=max_requests + 1,
            ),
        ),
        else_=1,
    )
    upsert = statement.on_conflict_do_update(
        index_elements=[PublicRateLimitBucket.bucket_key],
        set_={
            "window_id": statement.excluded.window_id,
            "request_count": next_count,
            "updated_at": statement.excluded.updated_at,
        },
    ).returning(PublicRateLimitBucket.request_count)

    try:
        request_count = db.execute(upsert).scalar_one()
        # Bounded garbage collection prevents one row per historical client
        # from accumulating forever, while limiting cleanup work per request.
        stale_keys = select(PublicRateLimitBucket.bucket_key).where(
            PublicRateLimitBucket.updated_at < current - timedelta(days=1)
        ).order_by(PublicRateLimitBucket.updated_at).limit(100)
        db.execute(delete(PublicRateLimitBucket).where(PublicRateLimitBucket.bucket_key.in_(stale_keys)))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Booking service is temporarily unavailable.",
            headers={"Retry-After": "1"},
        ) from None

    if request_count > max_requests:
        retry_after = max(1, math.ceil((window_id + 1) * window_seconds - current_time))
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def public_rate_limit(scope: str, *, max_requests: int, window_seconds: int = 60):
    """Build a FastAPI dependency for a public endpoint rate limit."""

    def dependency(request: Request, db: Session = Depends(get_db)) -> None:
        enforce_rate_limit(
            db,
            request,
            scope=scope,
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

    return dependency
