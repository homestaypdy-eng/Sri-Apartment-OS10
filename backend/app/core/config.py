from pydantic_settings import BaseSettings


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

    # Email (SMTP) — for sending quotations. Empty smtp_host means "not
    # configured"; the email-send endpoint returns a clear 501 rather than
    # silently failing or pretending to send.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "homestaypdy@gmail.com"
    smtp_use_tls: bool = True

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

    # Background reminder scheduler (see notifications/scheduler.py). Off by
    # default so running tests or a one-off script doesn't spawn a
    # background thread nobody asked for — a real deployment sets this true.
    enable_scheduler: bool = False
    reminder_scan_interval_minutes: int = 60

    # Razorpay — order creation needs a real merchant account (not available
    # in this sandbox: no credentials, and api.razorpay.com is outside this
    # environment's network allowlist). Webhook signature verification below
    # is pure HMAC crypto and works today with any secret, real or test.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
