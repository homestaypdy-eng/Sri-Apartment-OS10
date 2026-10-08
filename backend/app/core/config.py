import logging

from pydantic import field_validator
from pydantic_settings import BaseSettings

ZOTEL_SYNC_INTERVAL_DEFAULT_MINUTES = 15
ZOTEL_SYNC_INTERVAL_MIN_MINUTES = 5
ZOTEL_SYNC_INTERVAL_MAX_MINUTES = 1440
_config_logger = logging.getLogger("sri_apartment_os.config")


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/sri_apartment_os"

    # JWT
    jwt_secret_key: str = "CHANGE_ME_IN_PRODUCTION_use_env_var"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # App
    app_name: str = "Sri Apartment OS"
    environment: str = "development"

    # LOCAL EMAIL OTP TESTING (customer login only) — consulted ONLY when
    # environment != "production" (see customer_auth/router.py's
    # login_customer). The production gate itself never reads these; a
    # misconfigured value here cannot change production behavior at all.
    # "dev_code" (default): no email is ever attempted, the response just
    # carries dev_otp_code, exactly as before this setting existed.
    # "real_email": sends a real email SYNCHRONOUSLY, but only when the
    # customer's address exactly matches an entry in
    # customer_auth_email_otp_test_allowlist — an empty allowlist (the
    # default) means "real_email" mode can still never send to anyone.
    customer_auth_email_otp_mode: str = "dev_code"
    customer_auth_email_otp_test_allowlist: str = ""

    # Email (SMTP) — for sending quotations. Empty smtp_host means "not
    # configured"; the email-send endpoint returns a clear 501 rather than
    # silently failing or pretending to send.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "homestaypdy@gmail.com"
    smtp_use_tls: bool = True

    # Email provider switch — "gmail_smtp" (default, unchanged production
    # behavior) or "resend". send_email() in notifications/service.py
    # branches on this; every existing SMTP setting above stays exactly as
    # it is regardless of this value. resend_from_email is deliberately
    # separate from smtp_from_email (never falls back to it) — Resend can
    # only send from the domain verified with Resend (send.sriapartment.in),
    # never from the Gmail address.
    email_provider: str = "gmail_smtp"
    resend_api_key: str = ""
    resend_from_email: str = ""

    # WhatsApp click-to-chat — default country code prepended to 10-digit
    # phone numbers that don't already include one (see service.py).
    default_country_code: str = "91"

    # SMS gateway — generic HTTP API config (works with most Indian SMS
    # providers like MSG91/Textlocal, which expose a simple GET/POST API
    # keyed by an API key + sender ID). Empty sms_api_key means "not
    # configured" — same pattern as SMTP: real, working code, activated
    # by setting env vars in a real deployment, not a stub.
    sms_api_url: str = ""
    sms_api_key: str = ""
    sms_sender_id: str = "SRIAPT"

    # CORS — comma-separated origins allowed to call this API from a
    # browser. Defaults cover local dev only; a production deployment
    # MUST set this to its real frontend origin(s), or the API will
    # correctly refuse cross-origin requests from the deployed frontend.
    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000"

    # Base URL of the public customer-facing website — used to build the
    # link inside a password-reset email (Phase 3D), and (Phase 3G) every
    # redirect back to the frontend after a Google OAuth attempt
    # (oauth_router.py's _callback_redirect_url/_login_redirect_url). This
    # local dev default points at the website's known local origin; a
    # real deployment sets it to the production frontend domain (e.g.
    # https://sriapartment.in) — same "local default, real deployment
    # sets its own" pattern cors_allowed_origins above already uses.
    customer_portal_base_url: str = "http://localhost:5175"

    # Background reminder scheduler (see notifications/scheduler.py). Off by
    # default so running tests or a one-off script doesn't spawn a
    # background thread nobody asked for — a real deployment sets this true.
    enable_scheduler: bool = False
    reminder_scan_interval_minutes: int = 60
    zotel_sync_interval_minutes: int = ZOTEL_SYNC_INTERVAL_DEFAULT_MINUTES

    @field_validator("zotel_sync_interval_minutes", mode="before")
    @classmethod
    def _safe_zotel_sync_interval(cls, value):
        try:
            if isinstance(value, bool):
                raise ValueError("boolean is not an interval")
            interval = int(value)
            if isinstance(value, float) and not value.is_integer():
                raise ValueError("interval must be a whole number")
            if not ZOTEL_SYNC_INTERVAL_MIN_MINUTES <= interval <= ZOTEL_SYNC_INTERVAL_MAX_MINUTES:
                raise ValueError("interval is outside the safe range")
            return interval
        except (TypeError, ValueError):
            _config_logger.warning(
                "Invalid ZOTEL_SYNC_INTERVAL_MINUTES; using the safe default of %s minutes",
                ZOTEL_SYNC_INTERVAL_DEFAULT_MINUTES,
            )
            return ZOTEL_SYNC_INTERVAL_DEFAULT_MINUTES

    # Razorpay — order creation needs a real merchant account (not available
    # in this sandbox: no credentials, and api.razorpay.com is outside this
    # environment's network allowlist). Webhook signature verification below
    # is pure HMAC crypto and works today with any secret, real or test.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # AI Strategist narrative layer (Phase 13I) — real LLM provider
    # (Anthropic). Disabled by default: narrative_provider_enabled=False
    # means the router uses NullNarrativeProvider and every narrative
    # request returns the deterministic fallback, exactly like Phase
    # 13G's shipped behavior — same "empty/false = not configured"
    # convention as SMTP/SMS/Razorpay above. A fresh deployment with no
    # .env changes here is functionally identical to before this phase.
    narrative_provider_enabled: bool = False
    narrative_provider_api_key: str = ""
    narrative_provider_model: str = "claude-haiku-4-5-20251001"
    narrative_provider_timeout: float = 15.0

    # Zotel channel-manager integration (OTA availability/reservation sync).
    # Empty zotel_api_key means "not configured" — same convention as
    # SMTP/Razorpay/narrative provider above. zotel_base_url is per-property
    # (issued by the Zotel account manager, not a shared domain) and
    # zotel_hotel_id is the property's Zotel UUID — both required on every
    # request alongside the key/secret headers.
    zotel_base_url: str = ""
    zotel_api_key: str = ""
    zotel_api_secret: str = ""
    zotel_hotel_id: str = ""

    # Zotel HTTP client tuning (see zotel/client.py::_get). Defaults match the
    # previously-hardcoded constants, so an unchanged deployment behaves
    # exactly as before; a real deployment can override via env to adapt to
    # Zotel's observed rate limits / latency. Invalid/out-of-range values are
    # ignored by the client, which falls back to these same defaults.
    zotel_max_retries: int = 3
    zotel_timeout_seconds: float = 15.0

    # Optional lower-bound for which Zotel reservations to import, by CHECK-IN
    # date (ISO 8601, e.g. "2026-01-01"). Empty (the default) means "no lower
    # bound" — behaviour is identical to before this setting existed. When set,
    # it is passed as the Zotel API's checkin_from filter AND enforced again as
    # a local defense-in-depth gate on new imports (see zotel/service.py). An
    # unparseable value is ignored (treated as unset).
    zotel_import_from_date: str = ""

    # Automatic WhatsApp (booking-notification audit, notifications/service.py).
    # No WhatsApp provider is configured anywhere in this project — these
    # fields exist so the architecture supports one without inventing or
    # guessing which provider. The shape matches Meta's WhatsApp Business
    # Cloud API (the one provider this codebase's own pre-existing
    # documentation already named as "what true automatic WhatsApp would
    # need" — see notifications/service.py's module docstring): a
    # Meta-verified business phone number ("phone_number_id") and a
    # long-lived access token, POSTing to
    # https://graph.facebook.com/{version}/{phone_number_id}/messages.
    # Empty whatsapp_access_token means "not configured" — same honest
    # convention as SMTP/SMS/Razorpay/Zotel above: real, working code,
    # inert until a real Meta Business account and credentials exist.
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v19.0"

    # Phase 3G: customer-facing social login (Google — the only provider
    # this project supports). Empty client id/secret means "not
    # configured" — same convention as every other credential above
    # (SMTP/SMS/Razorpay/Zotel): the OAuth start endpoint returns a clear
    # 501 rather than redirecting to a provider it can't actually
    # complete a token exchange with. No hardcoded literal defaults, and
    # no secret is ever read by, or exposed to, the frontend — only
    # client_id (which is not secret; it's sent to the browser as part of
    # the redirect URL by design) ever leaves this process. The redirect
    # URI defaults to this project's own local dev backend origin;
    # production sets GOOGLE_REDIRECT_URI to its own public API domain
    # (e.g. https://api.sriapartment.in/api/v1/customer-auth/oauth/google/
    # callback) — local dev and production are just different values of
    # the same env var, never two different code paths. Must be
    # registered EXACTLY (including path) in Google Cloud Console, or
    # Google will refuse the request — see customer_auth/oauth_router.py's
    # module docstring for the full provider-console configuration a real
    # deployment must set.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/customer-auth/oauth/google/callback"

    # Initial-admin bootstrap password (RBAC Phase A hardening). Empty means
    # "not configured" — same convention as every credential above. Unlike
    # those, this one is only ever consumed once, at first seed, to create
    # the very first admin account (see db/seed.py); it is never read again
    # afterward and is not itself a runtime feature toggle. Deliberately no
    # hardcoded literal default here or in seed.py — a real deployment must
    # set this in .env before its first run, or admin bootstrap fails loudly
    # instead of silently using a guessable password.
    bootstrap_admin_password: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
