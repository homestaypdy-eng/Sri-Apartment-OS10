import ipaddress
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings

# Completes SQLAlchemy's Base.metadata for this process — see
# app/db/model_registry.py's docstring for why this is needed and why it
# lives there instead of here.
from app.db import model_registry as _model_registry  # noqa: F401

from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.bookings.router import router as bookings_router
# Phase 3B of the customer-auth foundation — registration only, no login
# yet. A separate router from staff auth and guest OTP/magic-link auth.
from app.modules.customer_auth.router import router as customer_auth_router
# Phase 3G: customer-facing Google OAuth login — a separate
# router (mirroring portal's own split into phone/email auth routers) so
# the core customer_auth router needs no changes for this.
from app.modules.customer_auth.oauth_router import router as customer_auth_oauth_router
from app.modules.finance.router import router as finance_router
from app.modules.housekeeping.router import router as housekeeping_router
from app.modules.inquiries.router import router as inquiries_router
from app.modules.inventory.router import router as inventory_router
from app.modules.maintenance.router import router as maintenance_router
from app.modules.notifications.router import router as notifications_router
from app.modules.notifications.scheduler import start_scheduler, stop_scheduler
from app.modules.ota.router import router as ota_router
from app.modules.payment_gateway.router import router as payment_gateway_router
from app.modules.payments.router import router as payments_router
from app.modules.portal.router import router as portal_router
from app.modules.portal.router import staff_router as guest_requests_router
from app.modules.zotel.router import router as zotel_router
# Phase 2 of the guest-auth redesign — mobile OTP only, a separate router
# from the live magic-link endpoints above (see auth_router.py docstring).
from app.modules.portal.auth_router import router as portal_phone_auth_router
# Phase 3 — email OTP, same separate-router approach as Phase 2.
from app.modules.portal.auth_email_router import router as portal_email_auth_router
from app.modules.properties.router import router as properties_router
from app.modules.public_booking.router import promo_admin_router, router as public_booking_router
from app.modules.reports.router import router as reports_router
from app.modules.settings.router import router as settings_router
from app.modules.staff.router import router as staff_router
from app.modules.staff_ops.router import router as staff_ops_router
from app.modules.users.router import router as users_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sri_apartment_os")

DEFAULT_JWT_SECRET = "CHANGE_ME_IN_PRODUCTION_use_env_var"


def _is_local_or_private_origin(origin: str) -> bool:
    """
    WEBSITE-04A: catches any local/LAN origin, not just the literal
    substrings "localhost"/"127.0.0.1" — a private network address like
    192.168.1.80 previously slipped through this guard undetected.
    """
    host = urlparse(origin).hostname or origin
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_private  # loopback, RFC1918, link-local, IPv6 equivalents
    except ValueError:
        return False  # a real hostname, e.g. a production domain


def _validate_production_config() -> None:
    """
    Raises RuntimeError to block startup when production-only security constraints
    are not met. Non-production environments log warnings instead (below in lifespan).
    Fail-fast is intentional: a misconfigured production deployment should refuse to
    serve traffic rather than silently run with weak credentials.
    """
    if settings.environment != "production":
        return
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "STARTUP BLOCKED: JWT_SECRET_KEY is still the default placeholder value. "
            "Set a real random secret via the JWT_SECRET_KEY environment variable before "
            "running in production."
        )
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    if not origins or all(_is_local_or_private_origin(o) for o in origins):
        raise RuntimeError(
            "STARTUP BLOCKED: CORS_ALLOWED_ORIGINS contains only localhost/private-network addresses "
            "in production. Set CORS_ALLOWED_ORIGINS to your real frontend domain(s) before running "
            "in production."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_production_config()  # raises RuntimeError → blocks startup with insecure prod config
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET:
        logger.warning(
            "SECURITY WARNING: JWT_SECRET_KEY is still set to its default placeholder value. "
            "Set a real, random secret via the JWT_SECRET_KEY environment variable before "
            "using this in any environment reachable by real users."
        )
    if settings.environment == "production":
        origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
        if any(_is_local_or_private_origin(o) for o in origins):
            logger.warning(
                "SECURITY WARNING: CORS_ALLOWED_ORIGINS still includes a localhost/private-network "
                "address while ENVIRONMENT=production. Set CORS_ALLOWED_ORIGINS to your real frontend "
                "domain(s)."
            )
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Catches anything that isn't already an HTTPException (those still go
    through FastAPI's normal handling, unaffected by this). Logs the real
    exception with a stack trace server-side for whoever is operating this
    deployment to actually see it, while the client only ever gets a
    generic message — never a raw stack trace, SQL error text, or file
    path, any of which could leak internals to whoever triggered the
    error, intentionally or not.
    """
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "An unexpected error occurred. Please try again or contact support if it persists."})


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Baseline security headers on every response. This does not replace
    TLS termination / HSTS at a reverse proxy or load balancer (this app
    doesn't terminate TLS itself), but these are cheap, real protections
    against a few common browser-side attack classes (clickjacking, MIME
    sniffing) regardless of what sits in front of it.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.include_router(auth_router)
app.include_router(customer_auth_router)
app.include_router(customer_auth_oauth_router)
app.include_router(properties_router)
app.include_router(bookings_router)
app.include_router(payments_router)
app.include_router(users_router)
app.include_router(housekeeping_router)
app.include_router(maintenance_router)
app.include_router(reports_router)
app.include_router(inquiries_router)
app.include_router(settings_router)
app.include_router(inventory_router)
app.include_router(staff_router)
app.include_router(staff_ops_router)
app.include_router(audit_router)
app.include_router(notifications_router)
app.include_router(payment_gateway_router)
app.include_router(ota_router)
app.include_router(zotel_router)
app.include_router(portal_router)
app.include_router(portal_phone_auth_router)
app.include_router(portal_email_auth_router)
app.include_router(guest_requests_router)
app.include_router(public_booking_router)
app.include_router(promo_admin_router)
app.include_router(finance_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment, "version": "1.0.0"}
