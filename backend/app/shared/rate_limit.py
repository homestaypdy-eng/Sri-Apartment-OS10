"""
Minimal in-memory rate limiter for the login endpoint. This is deliberately
simple — a per-process sliding window keyed by IP + email — not a
general-purpose rate-limiting framework. It's real protection against
naive brute-force login attempts, with an honest limitation stated
up front: it resets on process restart and doesn't share state across
multiple backend replicas. A production deployment running more than one
backend instance behind a load balancer should replace this with a
Redis-backed limiter (e.g. via slowapi or a reverse-proxy rate limit)
instead — this module is a genuine improvement over "no rate limiting at
all" (the previously documented gap), not a claim of enterprise-grade
DDoS protection.
"""
import time
from collections import defaultdict
from threading import Lock

_attempts: dict[str, list[float]] = defaultdict(list)
_lock = Lock()

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300  # 5 minutes


def is_rate_limited(key: str) -> bool:
    now = time.time()
    with _lock:
        attempts = [t for t in _attempts[key] if now - t < WINDOW_SECONDS]
        _attempts[key] = attempts
        return len(attempts) >= MAX_ATTEMPTS


def record_attempt(key: str) -> None:
    with _lock:
        _attempts[key].append(time.time())


def reset(key: str) -> None:
    """Called on a successful login — a legitimate login clears the
    failed-attempt counter for that key rather than making the user wait
    out the window even after proving they know the password."""
    with _lock:
        _attempts.pop(key, None)
