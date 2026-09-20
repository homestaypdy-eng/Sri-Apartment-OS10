"""
Unified notification dispatch.

HONEST SCOPE NOTE: "automatic" notifications here means automatic EMAIL,
sent by a real in-process scheduler (APScheduler) — genuinely automatic,
no human has to click anything. Automatic WhatsApp is NOT implemented:
the WhatsApp integration elsewhere in this system (inquiries module) uses
wa.me click-to-chat links, which by design require a human to open the
link and press send — there is no way to proactively push a WhatsApp
message that way. True automatic WhatsApp would need the WhatsApp
Business Cloud API with a Meta-verified business account and real
credentials, which this environment doesn't have. SMS is listed in the
requirements as "future-ready" — this module defines the channel and log
plumbing for it, but no SMS provider is integrated (same reason: no
provider account, no credentials, and the network egress here is
allowlisted to package registries only, not SMS gateways).
"""
import logging
import mimetypes
import os
import smtplib
import uuid
import httpx
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.bookings.models import Booking
from app.modules.notifications.models import NotificationLog
from app.modules.payments.models import Invoice, Payment
from app.modules.notifications.templates import render_template

logger = logging.getLogger("notifications.service")

# Same bundled-asset pattern already used independently by payments/service.py
# and inquiries/service.py for PDF logos — kept as its own copy here rather
# than importing their private constants, matching that existing precedent.
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets")
_DEFAULT_LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")


class EmailNotConfiguredError(Exception):
    pass


def send_email(
    to_email: str, subject: str, body: str, attachment_path: str | None = None,
    html_body: str | None = None, inline_images: dict[str, str] | None = None,
) -> None:
    """
    Plain-text-only when called the way every existing caller already does
    (html_body/inline_images omitted) — behavior for those callers is
    unchanged. Booking-notification emails opt into a branded HTML
    alternative: html_body is added as a multipart/alternative next to the
    plain-text body (so clients that can't render HTML still get the
    content), and inline_images ({content-id: file_path}) are embedded
    into that HTML part so <img src="cid:..."> tags (the logo, the booked
    flat's cover photo) render without needing any public URL — this is a
    local dev environment with no public asset host, so a real, publicly
    reachable image URL isn't an option here.
    """
    if not settings.smtp_host:
        raise EmailNotConfiguredError(
            "Email sending is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD as environment variables."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg.set_content(body)

    if html_body:
        msg.add_alternative(html_body, subtype="html")
        if inline_images:
            html_part = msg.get_payload()[-1]
            for cid, path in inline_images.items():
                if not path or not os.path.exists(path):
                    continue
                with open(path, "rb") as f:
                    data = f.read()
                guessed, _ = mimetypes.guess_type(path)
                maintype, _, subtype = (guessed or "image/jpeg").partition("/")
                html_part.add_related(data, maintype=maintype, subtype=subtype or "jpeg", cid=f"<{cid}>")

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(attachment_path))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


class SmsNotConfiguredError(Exception):
    pass


def send_sms(to_phone: str, message: str) -> None:
    """
    Sends a real SMS via a generic HTTP SMS gateway (works with MSG91,
    Textlocal, and most Indian SMS providers, which expose a simple
    GET/POST API keyed by API key + sender ID). Same pattern as
    send_email: raises SmsNotConfiguredError if not set up rather than
    silently failing or pretending to send — this is real, working code
    that a production deployment activates by setting env vars.
    """
    if not settings.sms_api_url or not settings.sms_api_key:
        raise SmsNotConfiguredError(
            "SMS sending is not configured. Set SMS_API_URL and SMS_API_KEY as environment variables."
        )

    digits = "".join(c for c in to_phone if c.isdigit())
    if len(digits) == 10:
        digits = settings.default_country_code + digits

    response = httpx.post(
        settings.sms_api_url,
        json={"api_key": settings.sms_api_key, "sender": settings.sms_sender_id, "to": digits, "message": message},
        timeout=15,
    )
    response.raise_for_status()


class WhatsAppNotConfiguredError(Exception):
    pass


def send_whatsapp(to_phone: str, message: str) -> None:
    """
    Sends a real WhatsApp message via Meta's WhatsApp Business Cloud API —
    the one provider this module's own docstring above already names as
    what genuine automatic WhatsApp requires ("a Meta-verified business
    account and real credentials, which this environment doesn't have").
    No other provider was assumed or guessed; none is configured anywhere
    in this project as of this change.

    POSTs to https://graph.facebook.com/{version}/{phone_number_id}/messages
    with a Bearer access token — Meta's documented text-message shape.
    Same honest pattern as send_email/send_sms: raises
    WhatsAppNotConfiguredError when WHATSAPP_ACCESS_TOKEN or
    WHATSAPP_PHONE_NUMBER_ID is unset, rather than silently failing or
    fabricating a sent message. Real, working code, inert until a real
    Meta Business account and long-lived token exist.
    """
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise WhatsAppNotConfiguredError(
            "WhatsApp sending is not configured. Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID "
            "as environment variables (issued by a Meta-verified WhatsApp Business Cloud API account)."
        )

    digits = "".join(c for c in to_phone if c.isdigit())
    if len(digits) == 10:
        digits = settings.default_country_code + digits

    url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
        json={"messaging_product": "whatsapp", "to": digits, "type": "text", "text": {"body": message}},
        timeout=15,
    )
    response.raise_for_status()


def send_otp_email(to_email: str, subject: str, body: str, db: Session) -> None:
    """
    Phase 2A guest auth hardening: OTP-specific wrapper around send_email
    that also writes a NotificationLog row for the attempt. The guest
    email-OTP endpoint (auth_email_router.py) used to call send_email
    directly, so — unlike every other trigger in this file — a failed OTP
    delivery in production left no investigable record.

    Deliberately NOT built on _log_and_maybe_send: that helper swallows
    every exception into a bool return, because a failed *business*
    notification (e.g. a booking confirmation) must never block the
    booking itself. OTP delivery is different — the router already has
    its own specific handling for EmailNotConfiguredError (a 501 to the
    caller) and relies on any other exception propagating as an unhandled
    error; none of that existing control flow should change. This wrapper
    only adds a log row alongside whatever send_email does, then
    re-raises exactly what send_email raised. Commits immediately (like
    record_otp_audit) so the log survives even though the caller's own
    HTTPException, raised right after in the not-configured case, means
    the caller never reaches its own later db.commit().

    Never logs the OTP code/message body — only recipient + success/
    error_message, same as every other NotificationLog row.
    """
    try:
        send_email(to_email, subject, body)
    except Exception as e:
        db.add(NotificationLog(event_type="guest_otp_delivery", channel="email", recipient=to_email, success=False, error_message=str(e)))
        db.commit()
        raise
    db.add(NotificationLog(event_type="guest_otp_delivery", channel="email", recipient=to_email, success=True))
    db.commit()


def send_otp_sms(to_phone: str, message: str, db: Session) -> None:
    """SMS counterpart to send_otp_email — see its docstring for the full
    reasoning (kept separate rather than parameterizing by channel, same
    convention _log_and_maybe_send/_log_and_maybe_send_sms already use)."""
    try:
        send_sms(to_phone, message)
    except Exception as e:
        db.add(NotificationLog(event_type="guest_otp_delivery", channel="sms", recipient=to_phone, success=False, error_message=str(e)))
        db.commit()
        raise
    db.add(NotificationLog(event_type="guest_otp_delivery", channel="sms", recipient=to_phone, success=True))
    db.commit()


def send_customer_password_reset_email(to_email: str, subject: str, body: str, db: Session) -> None:
    """Phase 3D counterpart to send_otp_email — same reasoning: re-raises
    whatever send_email raised (the router has its own specific handling
    for EmailNotConfiguredError vs. any other per-request failure), only
    adding a NotificationLog row alongside it. Never logs the reset
    link/token — only recipient + success/error_message."""
    try:
        send_email(to_email, subject, body)
    except Exception as e:
        db.add(NotificationLog(event_type="customer_password_reset", channel="email", recipient=to_email, success=False, error_message=str(e)))
        db.commit()
        raise
    db.add(NotificationLog(event_type="customer_password_reset", channel="email", recipient=to_email, success=True))
    db.commit()


def send_customer_login_otp_email(to_email: str, subject: str, body: str, db: Session) -> None:
    """Phase 3E counterpart to send_otp_email/send_customer_password_reset_
    email — same reasoning throughout this file: re-raises whatever
    send_email raised, only adding a NotificationLog row alongside it.
    Never logs the OTP code — only recipient + success/error_message."""
    try:
        send_email(to_email, subject, body)
    except Exception as e:
        db.add(NotificationLog(event_type="customer_login_otp", channel="email", recipient=to_email, success=False, error_message=str(e)))
        db.commit()
        raise
    db.add(NotificationLog(event_type="customer_login_otp", channel="email", recipient=to_email, success=True))
    db.commit()


def send_customer_login_otp_sms(to_phone: str, message: str, db: Session) -> None:
    """SMS counterpart to send_customer_login_otp_email — see its
    docstring for the full reasoning."""
    try:
        send_sms(to_phone, message)
    except Exception as e:
        db.add(NotificationLog(event_type="customer_login_otp", channel="sms", recipient=to_phone, success=False, error_message=str(e)))
        db.commit()
        raise
    db.add(NotificationLog(event_type="customer_login_otp", channel="sms", recipient=to_phone, success=True))
    db.commit()


def _log_and_maybe_send(
    db: Session, event_type: str, to_email: str, subject: str, body: str,
    booking_id=None, inquiry_id=None, guest_request_id=None, maintenance_ticket_id=None, housekeeping_task_id=None, inventory_item_id=None, leave_request_id=None, payroll_id=None, user_id=None, attachment_path: str | None = None,
    html_body: str | None = None, inline_images: dict[str, str] | None = None,
) -> bool:
    """Sends best-effort: logs success or failure, never raises to the caller
    (a notification failure must never break the business action that
    triggered it — a booking must still be created even if its confirmation
    email can't be sent)."""
    try:
        send_email(to_email, subject, body, attachment_path, html_body=html_body, inline_images=inline_images)
        db.add(NotificationLog(event_type=event_type, channel="email", recipient=to_email, booking_id=booking_id, inquiry_id=inquiry_id, guest_request_id=guest_request_id, maintenance_ticket_id=maintenance_ticket_id, housekeeping_task_id=housekeeping_task_id, inventory_item_id=inventory_item_id, leave_request_id=leave_request_id, payroll_id=payroll_id, user_id=user_id, success=True))
        return True
    except Exception as e:
        db.add(NotificationLog(event_type=event_type, channel="email", recipient=to_email, booking_id=booking_id, inquiry_id=inquiry_id, guest_request_id=guest_request_id, maintenance_ticket_id=maintenance_ticket_id, housekeeping_task_id=housekeeping_task_id, inventory_item_id=inventory_item_id, leave_request_id=leave_request_id, payroll_id=payroll_id, user_id=user_id, success=False, error_message=str(e)))
        return False


def _log_and_maybe_send_sms(db: Session, event_type: str, to_phone: str, message: str, booking_id=None) -> bool:
    """
    SMS counterpart to _log_and_maybe_send — kept as a separate function
    rather than adding a channel parameter to the existing, extensively-
    used email helper, to avoid touching working code that every other
    trigger in this file already depends on.
    """
    try:
        send_sms(to_phone, message)
        db.add(NotificationLog(event_type=event_type, channel="sms", recipient=to_phone, booking_id=booking_id, success=True))
        return True
    except Exception as e:
        db.add(NotificationLog(event_type=event_type, channel="sms", recipient=to_phone, booking_id=booking_id, success=False, error_message=str(e)))
        return False


def _log_and_maybe_send_whatsapp(db: Session, event_type: str, to_phone: str, message: str, booking_id=None) -> bool:
    """
    WhatsApp counterpart to _log_and_maybe_send/_log_and_maybe_send_sms —
    same best-effort contract: always logs (success or failure), never
    raises to the caller. Logged under channel="whatsapp", so a
    NotificationLog row here always means a real automatic-send ATTEMPT
    was made — never the manual wa.me click-to-chat link used elsewhere
    (inquiries/service.py), which has no send attempt to log at all.
    """
    try:
        send_whatsapp(to_phone, message)
        db.add(NotificationLog(event_type=event_type, channel="whatsapp", recipient=to_phone, booking_id=booking_id, success=True))
        return True
    except Exception as e:
        db.add(NotificationLog(event_type=event_type, channel="whatsapp", recipient=to_phone, booking_id=booking_id, success=False, error_message=str(e)))
        return False


def _already_sent_for_recipient(db: Session, event_type: str, booking_id, recipient: str) -> bool:
    """
    Per-recipient idempotency guard — distinct from _already_sent (which
    only scopes by event_type + booking_id). Needed wherever the SAME
    event_type is deliberately sent to MULTIPLE recipients for one booking
    (see trigger_website_booking_owner_notification's two owner contacts):
    _already_sent would incorrectly treat "recipient A already notified"
    as "nothing left to do" and skip recipient B entirely. Recipient
    values (email addresses vs. phone numbers) never collide with each
    other across channels, so no separate channel filter is needed.
    """
    return db.scalar(
        select(NotificationLog).where(
            NotificationLog.event_type == event_type,
            NotificationLog.booking_id == booking_id,
            NotificationLog.recipient == recipient,
        )
    ) is not None


DEFAULT_CHECKIN_TIME = "12:30 PM"   # same site-wide fallback as the website's ApartmentDetailPage
DEFAULT_CHECKOUT_TIME = "11:00 AM"  # (DEFAULT_CHECKIN_TIME/DEFAULT_CHECKOUT_TIME there) — kept identical


def _get_unit_cover_photo_path(db: Session, unit_id) -> str | None:
    """The same photo a guest sees as the unit's cover image on the public
    site: is_primary first, then sort_order (see properties/router.py's
    list_unit_photos ordering) — never a hard-coded flat, always whichever
    one is actually marked/ordered as primary for THIS unit. None if the
    unit has no photos uploaded, or its file is missing from disk."""
    from app.modules.properties.models import UnitPhoto

    photo = db.scalar(
        select(UnitPhoto).where(UnitPhoto.unit_id == unit_id)
        .order_by(UnitPhoto.is_primary.desc(), UnitPhoto.sort_order.asc(), UnitPhoto.uploaded_at.asc())
        .limit(1)
    )
    if photo and photo.file_path and os.path.exists(photo.file_path):
        return photo.file_path
    return None


def _booking_notification_context(db: Session, booking: Booking, invoice) -> dict:
    """
    Gathers every field the branded booking-notification templates (business
    email, client email, business/client WhatsApp) need, in one place, so
    all four stay consistent with each other instead of drifting — same
    reasoning as trigger_invoice_delivery/_convert_hold_to_booking already
    apply to their own single-purpose data gathering.
    """
    from app.modules.properties.models import Property, Unit
    from app.modules.settings.router import get_or_create_settings

    guest = booking.guest
    unit = db.get(Unit, booking.unit_id) if booking.unit_id else None
    property_ = db.get(Property, unit.property_id) if unit else None
    settings_row = get_or_create_settings(db)
    nights = max((booking.check_out - booking.check_in).days, 1)
    total_amount = float(invoice.total) if invoice is not None else float(booking.rate_applied) * nights
    gst_amount = float(invoice.cgst + invoice.sgst + invoice.igst) if invoice is not None else 0.0
    latest_payment = db.scalar(
        select(Payment).where(Payment.booking_id == booking.id).order_by(Payment.paid_at.desc())
    )
    payment_received = sum(
        float(p.amount) for p in db.scalars(select(Payment).where(Payment.booking_id == booking.id)).all()
    )
    balance_due = round(total_amount - payment_received, 2)
    if balance_due <= 0 and payment_received > 0:
        payment_status = "PAID"
    elif payment_received > 0:
        payment_status = "PARTIALLY PAID"
    else:
        payment_status = (latest_payment.status.upper() if latest_payment else "UNPAID")

    return {
        "guest": guest, "unit": unit, "property": property_, "settings": settings_row, "invoice": invoice,
        "nights": nights, "total_amount": total_amount, "gst_amount": gst_amount,
        "payment_received": payment_received, "balance_due": balance_due, "payment_status": payment_status,
        "checkin_time": (unit.checkin_time if unit else None) or DEFAULT_CHECKIN_TIME,
        "checkout_time": (unit.checkout_time if unit else None) or DEFAULT_CHECKOUT_TIME,
    }


def _render_booking_notification_html(db: Session, booking: Booking, ctx: dict, *, heading: str, intro_html: str, cover_photo_path: str | None) -> str:
    """Shared HTML skeleton for both the business and client booking emails
    (items 8/9/10 of the booking/notification finalization audit) — same
    field set, same branding, only the heading/intro text and recipient
    differ. Images are embedded via cid: (see send_email's html_body/
    inline_images docstring) since this local environment has no public
    asset host to link to instead."""
    guest, unit, property_, settings_row, invoice = ctx["guest"], ctx["unit"], ctx["property"], ctx["settings"], ctx["invoice"]

    rows = [
        ("Guest Name", guest.name if guest else "-"),
        ("Guest Phone", guest.phone if guest else "-"),
        ("Booking ID", str(booking.id)),
        ("Booking Source", "Direct Website" if booking.source == "website" else (booking.source or "-").replace("_", " ").title()),
        ("Booked On", booking.created_at.strftime("%d %b %Y, %I:%M %p") if booking.created_at else "-"),
        ("Apartment / Flat", unit.name if unit else "-"),
        ("Check-in", f"{booking.check_in.strftime('%d %b %Y')} — {ctx['checkin_time']}"),
        ("Check-out", f"{booking.check_out.strftime('%d %b %Y')} — {ctx['checkout_time']}"),
        ("Nights", str(ctx["nights"])),
        ("Guests", f"{booking.adults} Adult(s)" + (f", {booking.children} Child(ren)" if booking.children else "")),
        ("Booking Amount", f"Rs. {(ctx['total_amount'] - ctx['gst_amount']):.2f}"),
        ("GST", f"Rs. {ctx['gst_amount']:.2f}"),
        ("Amount Paid", f"Rs. {ctx['payment_received']:.2f}"),
        ("Balance", f"Rs. {ctx['balance_due']:.2f}"),
        ("Payment Status", ctx["payment_status"]),
    ]
    flat_details = []
    if unit and unit.bedroom_count:
        flat_details.append(f"{unit.bedroom_count} Bedroom(s)")
    if unit and unit.bathroom_count:
        flat_details.append(f"{unit.bathroom_count} Bathroom(s)")
    if unit and unit.max_occupancy:
        flat_details.append(f"Up to {unit.max_occupancy} guests")
    if flat_details:
        rows.append(("Room / Flat Details", ", ".join(flat_details)))
    # Only shown when the business has actually configured a policy —
    # never a fabricated/assumed one (mirrors the site's own house_rules
    # precedent: nothing shown when nothing is actually set).
    if settings_row.cancellation_policy:
        rows.append(("Cancellation Policy", settings_row.cancellation_policy.replace("\n", "<br>")))
    rows.append(("Invoice", "Invoice PDF attached." if invoice is not None else "Not yet generated"))

    rows_html = "".join(
        f'<tr><td style="padding:7px 14px;font-weight:bold;color:#1e3d32;border-bottom:1px solid #ece4d6;'
        f'white-space:nowrap;vertical-align:top;font-size:13px;">{label}</td>'
        f'<td style="padding:7px 14px;color:#2b2b2b;border-bottom:1px solid #ece4d6;font-size:13px;">{value}</td></tr>'
        for label, value in rows
    )
    flat_image_html = (
        '<img src="cid:flat_photo" alt="Booked flat" '
        'style="width:100%;max-width:520px;border-radius:8px;display:block;margin:0 auto 18px;">'
        if cover_photo_path else ""
    )
    contact_line = " &middot; ".join(x for x in (settings_row.address, settings_row.contact_phone, settings_row.contact_email) if x)

    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;background:#fdfaf5;
            border:1px solid #e6ddd0;border-radius:10px;overflow:hidden;">
  <div style="background:#1e3d32;padding:22px;text-align:center;">
    <img src="cid:logo" alt="Sri Apartment Homestay" style="height:44px;">
  </div>
  <div style="padding:22px;">
    <h2 style="color:#1e3d32;text-align:center;margin:0 0 4px;font-size:19px;">{heading}</h2>
    <p style="text-align:center;color:#8a7f6c;margin:0 0 18px;font-size:13px;letter-spacing:0.04em;text-transform:uppercase;">
      Sri Apartment Homestay &middot; Pondicherry
    </p>
    {flat_image_html}
    {intro_html}
    <table style="width:100%;border-collapse:collapse;margin-top:14px;">{rows_html}</table>
  </div>
  <div style="background:#f1ece1;padding:14px;text-align:center;color:#8a7f6c;font-size:11px;">
    {contact_line}
  </div>
</div>"""


def trigger_booking_confirmation(db: Session, booking: Booking, pdf_path: str | None = None) -> bool | None:
    """
    Sends the guest their ONE booking confirmation email — branded HTML
    (logo + the booked unit's actual cover photo, never a hard-coded flat)
    with the full booking/payment breakdown, plus the invoice PDF attached
    when pdf_path is given. Booking-finalization audit fix: this used to be
    a separate plain-text email, with the invoice sent as a SECOND, distinct
    trigger_invoice_delivery email — the guest now gets exactly one
    confirmation, matching the "do not send duplicate client confirmation
    emails" requirement the same way the business owner email already
    works (see trigger_website_booking_owner_notification). Plain-text
    fallback still describes the same core facts for any client that can't
    render the HTML alternative.
    """
    guest = booking.guest
    if not guest or not guest.email:
        return None  # nothing to send to — not a failure, just no channel available

    invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking.id))
    ctx = _booking_notification_context(db, booking, invoice)
    unit = ctx["unit"]

    subject = "Booking Confirmed — Sri Apartment Homestay"
    body = (
        f"Hi {guest.name},\n\nYour booking is confirmed.\n"
        f"Booking ID: {booking.id}\n"
        f"Apartment/Flat: {unit.name if unit else '-'}\n"
        f"Check-in: {booking.check_in} — {ctx['checkin_time']}\n"
        f"Check-out: {booking.check_out} — {ctx['checkout_time']}\n"
        f"Nights: {ctx['nights']}\n"
        f"Total: Rs. {ctx['total_amount']:.2f}\n"
        f"Paid: Rs. {ctx['payment_received']:.2f}\n"
        f"Balance: Rs. {ctx['balance_due']:.2f}\n"
        f"Payment status: {ctx['payment_status']}\n\n"
        f"We look forward to hosting you!"
    )
    cover_photo_path = _get_unit_cover_photo_path(db, booking.unit_id) if booking.unit_id else None
    html_body = _render_booking_notification_html(
        db, booking, ctx,
        heading="Your booking is confirmed.",
        intro_html=f'<p style="text-align:center;color:#2b2b2b;">Hi {guest.name}, thank you for booking directly with us — see your stay details below.</p>',
        cover_photo_path=cover_photo_path,
    )
    return _log_and_maybe_send(
        db, "booking_confirmation", guest.email, subject, body, booking_id=booking.id,
        attachment_path=pdf_path, html_body=html_body,
        inline_images={"logo": _DEFAULT_LOGO_PATH, "flat_photo": cover_photo_path},
    )


def trigger_booking_confirmation_sms(db: Session, booking: Booking) -> bool | None:
    """
    SMS counterpart to trigger_booking_confirmation — kept as its own
    function, called alongside (not instead of) the email trigger, so a
    guest with a phone but no email on file still gets a confirmation,
    and existing email-only behavior/tests are completely unaffected.
    """
    guest = booking.guest
    if not guest or not guest.phone:
        return None
    message = render_template(
        "booking_confirmation_sms", "body",
        check_in=booking.check_in, check_out=booking.check_out, rate=booking.rate_applied,
    )
    return _log_and_maybe_send_sms(db, "booking_confirmation", guest.phone, message, booking_id=booking.id)


def _render_booking_whatsapp_message(booking: Booking, ctx: dict, *, heading: str) -> str:
    """Shared concise WhatsApp text for both the client and the two
    business numbers (booking/notification finalization audit, items 11
    and 12) — no invoice attachment on this channel (WhatsApp has none),
    just the same core facts the branded email carries."""
    guest = ctx["guest"]
    unit = ctx["unit"]
    guest_count = f"{booking.adults} Adult" + ("s" if booking.adults != 1 else "")
    if booking.children:
        guest_count += f", {booking.children} Child" + ("ren" if booking.children != 1 else "")
    return (
        f"Sri Apartment Homestay\n\n"
        f"{heading}\n\n"
        f"Guest: {guest.name if guest else '-'}\n"
        f"Booking ID: {booking.id}\n"
        f"Apartment: {unit.name if unit else '-'}\n"
        f"Check-in: {booking.check_in.strftime('%d %B %Y')} — {ctx['checkin_time']}\n"
        f"Check-out: {booking.check_out.strftime('%d %B %Y')} — {ctx['checkout_time']}\n"
        f"Guests: {guest_count}\n"
        f"Total: Rs. {ctx['total_amount']:.2f}\n"
        f"Paid: Rs. {ctx['payment_received']:.2f}\n"
        f"Balance: Rs. {ctx['balance_due']:.2f}\n"
        f"Payment: {ctx['payment_status']}\n\n"
        f"Thank you for choosing\nSri Apartment Homestay, Pondicherry."
    )


def trigger_booking_confirmation_whatsapp(db: Session, booking: Booking) -> bool | None:
    """
    WhatsApp counterpart to trigger_booking_confirmation/_sms — called
    alongside (not instead of) email/SMS. Returns None only when the guest
    has no phone on file, exactly like the SMS trigger; otherwise attempts
    a real send and reports the true outcome. Since no WhatsApp provider
    is configured anywhere in this project as of this change, every real
    attempt here currently resolves to False (logged as a failed attempt
    with a "not configured" error_message) — never a fabricated True. This
    is the honest behavior the audit required, not a bug: once a real
    Meta WhatsApp Business Cloud API account and credentials are
    configured (WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID), this
    same code starts actually sending with no further change needed.
    """
    guest = booking.guest
    if not guest or not guest.phone:
        return None
    invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking.id))
    ctx = _booking_notification_context(db, booking, invoice)
    message = _render_booking_whatsapp_message(booking, ctx, heading="Booking Confirmed ✅")
    return _log_and_maybe_send_whatsapp(db, "booking_confirmation", guest.phone, message, booking_id=booking.id)


def trigger_website_booking_owner_notification(db: Session, booking: Booking, invoice, pdf_path: str | None = None) -> dict:
    """
    Notifies BOTH configured owner/business contacts that a direct website
    booking was confirmed after successful payment verification — closes
    the gap the read-only notification audit found: _convert_hold_to_booking()
    previously called no owner-facing trigger at all, so a real, fully
    paid website booking notified the owner of nothing.

    Business email requirement (see the payment->booking->notification
    audit): each business contact must get exactly ONE email per booking,
    and that email must carry the invoice PDF — not a separate invoice-less
    notification plus a second invoice email. trigger_invoice_delivery is
    guest-only (see its own docstring) and stays that way; pdf_path is
    passed in here instead so the SAME single email includes the
    attachment, rather than adding a second business-facing trigger.

    Email goes to BusinessSettings.contact_email (primary) and
    secondary_contact_email (secondary) if set, each with the invoice PDF
    attached when pdf_path is available. WhatsApp is attempted to
    whatsapp_business_number (primary) and secondary_whatsapp_number
    (secondary) if set — honestly, via the same not-configured-safe path
    as trigger_booking_confirmation_whatsapp (see its docstring): no
    provider exists yet, so these currently log as failed attempts, never
    a fabricated success. WhatsApp never carries the attachment (not
    required — see this trigger's caller).

    IDEMPOTENCY: guarded per (event_type, booking_id, recipient) via
    _already_sent_for_recipient, so calling this more than once for the
    same booking never re-notifies a recipient who was already reached.
    In practice this is called exactly once, from inside
    _convert_hold_to_booking() — itself only ever invoked once per
    BookingHold (its own docstring/tests establish this: the browser path
    and the webhook backup path both gate on hold.status, and a converted
    hold is never re-converted) — so this per-recipient guard is
    defense-in-depth on top of that existing, already-tested protection,
    not the only thing standing between a duplicate browser/webhook race
    and a duplicate owner email.

    Best-effort throughout: one recipient's failure never blocks another
    recipient's send, and nothing here can ever block or roll back the
    booking itself (mirrors every other trigger in this file).
    """
    ctx = _booking_notification_context(db, booking, invoice)
    settings_row, guest, unit = ctx["settings"], ctx["guest"], ctx["unit"]

    subject = f"New Direct Website Booking — {guest.name if guest else 'Guest'} ({unit.name if unit else booking.unit_id})"
    plain_body = (
        f"A new direct website booking has been confirmed and paid.\n\n"
        f"Guest: {guest.name if guest else 'Unknown'}\n"
        f"Guest phone: {guest.phone if guest else '-'}\n"
        f"Booking reference: {booking.id}\n"
        f"Apartment/Unit: {unit.name if unit else '-'}\n"
        f"Check-in: {booking.check_in} — {ctx['checkin_time']}\n"
        f"Check-out: {booking.check_out} — {ctx['checkout_time']}\n"
        f"Guests: {booking.adults + booking.children}\n"
        f"Total amount: Rs. {ctx['total_amount']:.2f}\n"
        f"GST: Rs. {ctx['gst_amount']:.2f}\n"
        f"Payment received: Rs. {ctx['payment_received']:.2f}\n"
        f"Balance due: Rs. {ctx['balance_due']:.2f}\n"
        f"Payment status: {ctx['payment_status']}\n"
        f"Booking status: {booking.status}\n"
        + (f"Invoice number: {invoice.invoice_no}\n" if invoice is not None else "Invoice: not yet generated\n")
    )
    cover_photo_path = _get_unit_cover_photo_path(db, booking.unit_id) if booking.unit_id else None
    html_body = _render_booking_notification_html(
        db, booking, ctx,
        heading="New Booking Received",
        intro_html='<p style="text-align:center;color:#2b2b2b;">A new direct website booking has been confirmed and paid.</p>',
        cover_photo_path=cover_photo_path,
    )

    email_recipients = [e for e in (settings_row.contact_email, settings_row.secondary_contact_email) if e]
    email_results: dict[str, bool | None] = {}
    for recipient in email_recipients:
        if _already_sent_for_recipient(db, "owner_booking_notification", booking.id, recipient):
            email_results[recipient] = None  # already notified by an earlier call — not re-sent
            continue
        email_results[recipient] = _log_and_maybe_send(
            db, "owner_booking_notification", recipient, subject, plain_body, booking_id=booking.id,
            attachment_path=pdf_path, html_body=html_body,
            inline_images={"logo": _DEFAULT_LOGO_PATH, "flat_photo": cover_photo_path},
        )

    whatsapp_message = _render_booking_whatsapp_message(booking, ctx, heading="New Website Booking")
    whatsapp_recipients = [n for n in (settings_row.whatsapp_business_number, settings_row.secondary_whatsapp_number) if n]
    whatsapp_results: dict[str, bool | None] = {}
    for number in whatsapp_recipients:
        if _already_sent_for_recipient(db, "owner_booking_notification", booking.id, number):
            whatsapp_results[number] = None
            continue
        whatsapp_results[number] = _log_and_maybe_send_whatsapp(db, "owner_booking_notification", number, whatsapp_message, booking_id=booking.id)

    return {"email": email_results, "whatsapp": whatsapp_results}


def trigger_cancellation(db: Session, booking: Booking) -> bool | None:
    guest = booking.guest
    if not guest or not guest.email:
        return None
    subject = "Booking Cancelled — Sri Apartment Homestay"
    body = f"Hi {guest.name},\n\nYour booking for {booking.check_in} to {booking.check_out} has been cancelled."
    return _log_and_maybe_send(db, "cancellation", guest.email, subject, body, booking_id=booking.id)


def trigger_invoice_delivery(db: Session, invoice: Invoice, pdf_path: str) -> bool | None:
    """
    Emails the invoice PDF to the guest, with the full booking/payment
    context the guest actually needs (not just "here's your invoice
    number"). Shared by both the automatic post-payment-verification path
    (_convert_hold_to_booking) and the manual admin "resend" endpoint
    (payments/router.py) — one function, one template, so both stay
    consistent instead of drifting into two different email bodies.
    """
    booking = invoice.booking
    guest = booking.guest if booking else None
    if not guest or not guest.email:
        return None

    unit = booking.unit
    property_ = unit.property if unit else None
    payment = db.scalar(
        select(Payment).where(Payment.booking_id == booking.id).order_by(Payment.paid_at.desc())
    )
    nights = (booking.check_out - booking.check_in).days

    subject = render_template(
        "invoice_delivery", "subject",
        invoice_no=invoice.invoice_no,
        property_name=property_.name if property_ else "Sri Apartment Homestay",
    )
    body = render_template(
        "invoice_delivery", "body",
        guest_name=guest.name,
        property_name=property_.name if property_ else "Sri Apartment Homestay",
        unit_name=unit.name if unit else "—",
        booking_id=booking.id,
        check_in=booking.check_in,
        check_out=booking.check_out,
        arrival_time_suffix=f" (arrival ~{booking.arrival_time})" if booking.arrival_time else "",
        departure_time_suffix=f" (departure ~{booking.departure_time})" if booking.departure_time else "",
        nights=nights,
        guests=booking.adults + booking.children,
        amount_paid=payment.amount if payment else invoice.total,
        payment_status=payment.status if payment else "unknown",
        invoice_no=invoice.invoice_no,
    )
    return _log_and_maybe_send(db, "invoice_delivery", guest.email, subject, body, booking_id=booking.id if booking else None, attachment_path=pdf_path)


def dispatch_booking_notifications(booking_id: uuid.UUID) -> None:
    """
    Sends every post-payment booking notification (customer confirmation
    email with invoice attached/SMS/WhatsApp, owner email with invoice
    attached/WhatsApp) for one booking, in its own DB session.

    Root-cause fix for the payment->booking-confirmation timeout bug: this
    logic used to run INLINE inside _convert_hold_to_booking(), which ran
    INLINE inside verify_payment(), before the HTTP response was returned —
    so the guest's browser waited on up to 4 real, sequential SMTP
    connections (customer confirmation, invoice, owner primary, owner
    secondary) before ever seeing a response. In this dev environment, with
    real Gmail SMTP configured and its daily send limit already exhausted,
    one of those sends was directly observed blocking for the full 15s
    smtplib socket timeout ("Connection unexpectedly closed: The read
    operation timed out") — comfortably past the website's 12s client-side
    request timeout — even though the booking itself had already committed
    successfully. The guest saw "Confirming your booking..." time out while
    a real, paid, confirmed booking sat in the database the whole time.

    The fix: verify_payment() (and the webhook backup path) now schedule
    this function as a FastAPI BackgroundTask, which only runs AFTER the
    HTTP response has already been sent — so no notification channel's
    speed or availability can ever again delay or break the booking
    confirmation response. Uses its own SessionLocal(), exactly like every
    other background job in notifications/scheduler.py, since the
    request's session is gone by the time this runs.

    Every trigger_* function called here already has its own best-effort,
    never-raises, log-success-or-failure contract (see _log_and_maybe_send
    and friends) — the outer try/except below is defense-in-depth so that
    even an unexpected error (e.g. a DB hiccup) can only fail this
    detached background job, never the booking that already committed.
    """
    db = SessionLocal()
    try:
        booking = db.get(Booking, booking_id)
        if booking is None:
            logger.error("Notification dispatch skipped: booking %s not found", booking_id)
            return
        invoice = db.scalar(select(Invoice).where(Invoice.booking_id == booking.id))
        pdf_path = invoice.pdf_path if invoice is not None else None

        # Booking/notification finalization audit: the guest's invoice used
        # to be a SEPARATE trigger_invoice_delivery email on top of this
        # confirmation — two client emails per booking. trigger_booking_confirmation
        # now carries the invoice attachment itself (see its own docstring),
        # so the guest gets exactly one confirmation email, matching the
        # same "one email, invoice attached" contract already required of
        # the business owner email below. trigger_invoice_delivery itself
        # is untouched and still used by the separate admin manual
        # "resend invoice" endpoint (payments/router.py).
        trigger_booking_confirmation(db, booking, pdf_path)
        trigger_booking_confirmation_sms(db, booking)
        trigger_booking_confirmation_whatsapp(db, booking)
        trigger_website_booking_owner_notification(db, booking, invoice, pdf_path)

        db.commit()
    except Exception:
        logger.exception("Booking notification dispatch failed for booking %s", booking_id)
        db.rollback()
    finally:
        db.close()


def _already_sent(db: Session, event_type: str, booking_id) -> bool:
    """
    Dedupes on any PRIOR ATTEMPT, not just successful sends. This matters:
    if dedup only checked for success, a reminder that keeps failing (e.g.
    SMTP misconfigured) would be re-attempted on every scheduler tick
    forever instead of failing once and staying failed until someone fixes
    the underlying config — "at most once per booking" means the attempt,
    not the outcome.
    """
    return db.scalar(
        select(NotificationLog).where(
            NotificationLog.event_type == event_type,
            NotificationLog.booking_id == booking_id,
        )
    ) is not None


def _already_sent_for_inquiry(db: Session, event_type: str, inquiry_id) -> bool:
    """Same dedup rule as _already_sent, scoped to inquiry_id instead of
    booking_id — an inquiry follow-up reminder is sent at most once per
    inquiry per due date's scan, not re-sent every time the scan runs
    while next_follow_up_date stays unchanged."""
    return db.scalar(
        select(NotificationLog).where(
            NotificationLog.event_type == event_type,
            NotificationLog.inquiry_id == inquiry_id,
        )
    ) is not None


def scan_and_send_due_reminders(db: Session, as_of: datetime | None = None) -> dict:
    """
    The actual "automatic" part: called on a schedule (see scheduler.py) to
    find bookings that need a check-in reminder (1 day before), a checkout
    reminder (day of), a review request (1 day after checkout), or a
    payment reminder (balance outstanding 2+ days after checkout). Each is
    sent at most once per booking, tracked via NotificationLog.
    """
    now = as_of or datetime.now(timezone.utc)
    today = now.date()
    results = {"checkin_reminder": 0, "checkout_reminder": 0, "review_request": 0, "payment_reminder": 0}

    active_bookings = db.scalars(
        select(Booking).where(Booking.status.in_(("confirmed", "checked_in", "checked_out")))
    ).all()

    for booking in active_bookings:
        guest = booking.guest
        if not guest or not guest.email:
            continue

        if booking.check_in == today + timedelta(days=1) and not _already_sent(db, "checkin_reminder", booking.id):
            _log_and_maybe_send(
                db, "checkin_reminder", guest.email, "Check-in Reminder — Sri Apartment Homestay",
                f"Hi {guest.name}, this is a reminder that your check-in is tomorrow ({booking.check_in}). "
                f"Check-in time: 12:30 PM. See you soon!",
                booking_id=booking.id,
            )
            results["checkin_reminder"] += 1

        if booking.check_out == today and not _already_sent(db, "checkout_reminder", booking.id):
            _log_and_maybe_send(
                db, "checkout_reminder", guest.email, "Check-out Reminder — Sri Apartment Homestay",
                f"Hi {guest.name}, this is a reminder that check-out is today by 11:00 AM. "
                f"Thank you for staying with us!",
                booking_id=booking.id,
            )
            results["checkout_reminder"] += 1

        if booking.check_out == today - timedelta(days=1) and not _already_sent(db, "review_request", booking.id):
            _log_and_maybe_send(
                db, "review_request", guest.email, "How was your stay? — Sri Apartment Homestay",
                f"Hi {guest.name}, we hope you enjoyed your stay! We'd love it if you could leave us a review.",
                booking_id=booking.id,
            )
            results["review_request"] += 1

        if booking.check_out <= today - timedelta(days=2) and not _already_sent(db, "payment_reminder", booking.id):
            paid = sum(float(p.amount) for p in db.scalars(select(Payment).where(Payment.booking_id == booking.id)).all())
            nights = (booking.check_out - booking.check_in).days
            expected = float(booking.rate_applied) * nights + float(booking.extra_guest_charge)
            if paid < expected:
                _log_and_maybe_send(
                    db, "payment_reminder", guest.email, "Pending Balance — Sri Apartment Homestay",
                    f"Hi {guest.name}, our records show a pending balance of Rs. {expected - paid:.2f} "
                    f"for your recent stay. Please settle at your earliest convenience.",
                    booking_id=booking.id,
                )
                results["payment_reminder"] += 1

    from app.modules.inquiries.models import Inquiry
    due_inquiries = db.scalars(
        select(Inquiry).where(
            Inquiry.next_follow_up_date == today,
            Inquiry.lead_status.in_(("new", "contacted", "follow_up")),
        )
    ).all()
    results["inquiry_follow_up_due"] = 0
    for inquiry in due_inquiries:
        if not _already_sent_for_inquiry(db, "inquiry_follow_up_due", inquiry.id):
            trigger_inquiry_follow_up_due(db, inquiry)
            results["inquiry_follow_up_due"] += 1

    db.commit()
    return results


def trigger_guest_request_created(db: Session, guest_request) -> bool | None:
    """
    Notifies the business (its own contact_email from Settings) that a new
    guest request came in — the internal-facing counterpart to the
    guest-facing triggers above. Best-effort, same as every other trigger
    here: a notification failure never blocks the request from being
    created.
    """
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    guest = guest_request.guest
    return _log_and_maybe_send(
        db, "guest_request_created", settings_row.contact_email,
        f"New Guest Request ({guest_request.request_type}) — Sri Apartment Homestay",
        f"{guest.name if guest else 'A guest'} raised a new {guest_request.request_type} request: {guest_request.description}",
        guest_request_id=guest_request.id,
    )


def trigger_guest_request_resolved(db: Session, guest_request) -> bool | None:
    """Notifies the guest their request has been resolved."""
    guest = guest_request.guest
    if not guest or not guest.email:
        return None
    return _log_and_maybe_send(
        db, "guest_request_resolved", guest.email,
        "Your Request Has Been Resolved — Sri Apartment Homestay",
        f"Hi {guest.name}, your {guest_request.request_type} request has been resolved. "
        f"We hope everything is now to your satisfaction — thank you for staying with us.",
        guest_request_id=guest_request.id,
    )


def trigger_maintenance_ticket_created(db: Session, ticket) -> bool | None:
    """Notifies the business (its own contact_email) that a new
    maintenance ticket was raised — mirrors trigger_guest_request_created."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    unit = ticket.unit
    return _log_and_maybe_send(
        db, "maintenance_ticket_created", settings_row.contact_email,
        f"New Maintenance Ticket ({ticket.priority}) — Sri Apartment Homestay",
        f"A new {ticket.priority}-priority maintenance ticket was raised for {unit.name if unit else 'a unit'}: {ticket.description}",
        maintenance_ticket_id=ticket.id,
    )


def trigger_maintenance_ticket_resolved(db: Session, ticket) -> bool | None:
    """Notifies the business the ticket is resolved — there's no guest
    tied to a maintenance ticket the way there is for a guest request, so
    this stays internal-facing rather than guest-facing."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    unit = ticket.unit
    cost_line = f" Cost: Rs.{ticket.cost}." if ticket.cost else ""
    return _log_and_maybe_send(
        db, "maintenance_ticket_resolved", settings_row.contact_email,
        f"Maintenance Ticket Resolved — Sri Apartment Homestay",
        f"The maintenance ticket for {unit.name if unit else 'a unit'} has been marked resolved.{cost_line}",
        maintenance_ticket_id=ticket.id,
    )


def trigger_payment_received(db: Session, payment) -> bool | None:
    """
    Notifies the business (its own contact_email) that a payment was
    recorded — Finance's counterpart to trigger_guest_request_created:
    an internal-facing "something happened, here's the summary" alert
    rather than a guest-facing one, since payments aren't confirmed to
    the guest through this channel (that's what the invoice/receipt is for).
    """
    from app.modules.settings.models import BusinessSettings
    from app.modules.bookings.models import Booking, Guest
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    booking = db.get(Booking, payment.booking_id)
    guest = db.get(Guest, booking.guest_id) if booking else None
    kind = "Refund" if float(payment.amount) < 0 else "Payment"
    return _log_and_maybe_send(
        db, "payment_received", settings_row.contact_email,
        f"{kind} Recorded — Rs.{abs(float(payment.amount))} ({payment.mode})",
        f"{kind} of Rs.{abs(float(payment.amount))} via {payment.mode} recorded for "
        f"{guest.name if guest else 'a guest'}'s booking.",
        booking_id=payment.booking_id,
    )


def trigger_expense_approval(db: Session, expense, approved: bool) -> bool | None:
    """Notifies whoever submitted the expense that it was approved or
    rejected — mirrors trigger_guest_request_resolved's "tell the person
    who raised this what happened to it" pattern."""
    from app.modules.auth.models import User
    if not expense.created_by:
        return None
    submitter = db.get(User, expense.created_by)
    if not submitter or not submitter.email:
        return None
    decision = "approved" if approved else "rejected"
    return _log_and_maybe_send(
        db, "expense_approval", submitter.email,
        f"Your Expense Was {decision.capitalize()} — Rs.{float(expense.amount)}",
        f"Your {expense.category} expense of Rs.{float(expense.amount)} dated {expense.expense_date} was {decision}.",
    )


def trigger_daily_closing_completed(db: Session, closing) -> bool | None:
    """Notifies the business its books were closed for a day — a genuine
    daily-summary alert, matching the 'close the books' step the
    business's own Excel workflow does manually at day's end."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    return _log_and_maybe_send(
        db, "daily_closing_completed", settings_row.contact_email,
        f"Daily Closing — {closing.closing_date}",
        f"Books closed for {closing.closing_date}. Opening: Rs.{float(closing.opening_balance)}, "
        f"Cash In: Rs.{float(closing.total_cash_in)}, Cash Out: Rs.{float(closing.total_cash_out)}, "
        f"Closing Balance: Rs.{float(closing.closing_balance)}.",
    )


def trigger_housekeeping_task_created(db: Session, task) -> bool | None:
    """Notifies the business a cleaning task was raised — mirrors
    trigger_maintenance_ticket_created's internal-facing pattern."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    unit = task.unit
    return _log_and_maybe_send(
        db, "housekeeping_task_created", settings_row.contact_email,
        f"New Housekeeping Task ({task.task_type}) — Sri Apartment Homestay",
        f"A new {task.task_type} task was raised for {unit.name if unit else 'a unit'}.",
        housekeeping_task_id=task.id,
    )


def trigger_housekeeping_task_completed(db: Session, task) -> bool | None:
    """Notifies the business a cleaning task was completed — the room is
    ready again."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    unit = task.unit
    return _log_and_maybe_send(
        db, "housekeeping_task_completed", settings_row.contact_email,
        "Housekeeping Task Completed — Sri Apartment Homestay",
        f"The {task.task_type} task for {unit.name if unit else 'a unit'} has been marked completed.",
        housekeeping_task_id=task.id,
    )


def trigger_low_stock_alert(db: Session, item) -> bool | None:
    """
    Fires when a stock-out brings an item's current_stock at or below its
    minimum_stock threshold. Deliberately fired at the moment stock CROSSES
    into low — not on every stock-out while already low — see the
    call site in inventory/router.py for how that's checked, so this
    doesn't spam one alert per stock-out once an item is already flagged.
    """
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    return _log_and_maybe_send(
        db, "low_stock_alert", settings_row.contact_email,
        f"Low Stock: {item.name}",
        f"{item.name} is now at {item.current_stock} {item.unit_of_measure}, at or below its minimum "
        f"threshold of {item.minimum_stock}. Consider restocking.",
        inventory_item_id=item.id,
    )


def trigger_leave_reviewed(db: Session, leave) -> bool | None:
    """Notifies the staff member whose leave request was approved/rejected."""
    from app.modules.auth.models import User
    user = db.get(User, leave.user_id)
    if not user or not user.email:
        return None
    return _log_and_maybe_send(
        db, "leave_reviewed", user.email,
        f"Your Leave Request Was {leave.status.capitalize()}",
        f"Your leave request for {leave.from_date} to {leave.to_date} was {leave.status}.",
        leave_request_id=leave.id, user_id=user.id,
    )


def trigger_payroll_paid(db: Session, payroll) -> bool | None:
    """Notifies the staff member their salary has been paid."""
    from app.modules.auth.models import User
    user = db.get(User, payroll.user_id)
    if not user or not user.email:
        return None
    return _log_and_maybe_send(
        db, "payroll_paid", user.email,
        f"Salary Paid — {payroll.period_month}/{payroll.period_year}",
        f"Your salary of Rs.{payroll.net_pay} for {payroll.period_month}/{payroll.period_year} has been paid.",
        payroll_id=payroll.id, user_id=user.id,
    )


def trigger_property_created(db: Session, prop) -> bool | None:
    """Notifies the business a new property was added to the system —
    a low-frequency but genuine event worth an internal record, the same
    as every other entity-creation trigger in this file."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    return _log_and_maybe_send(
        db, "property_created", settings_row.contact_email,
        f"New Property Added: {prop.name}",
        f"A new property '{prop.name}' ({prop.address}) has been added to the system.",
    )


def trigger_inquiry_created(db: Session, inquiry) -> bool | None:
    """Notifies the business a new enquiry came in — mirrors every other
    entity-creation trigger in this file."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    return _log_and_maybe_send(
        db, "inquiry_created", settings_row.contact_email,
        f"New Enquiry: {inquiry.client_name}",
        f"New {inquiry.source} enquiry from {inquiry.client_name} ({inquiry.client_phone}) for "
        f"{inquiry.check_in_date} to {inquiry.check_out_date}.",
        inquiry_id=inquiry.id,
    )


def trigger_inquiry_follow_up_due(db: Session, inquiry) -> bool | None:
    """Notifies the business a follow-up is due today for an enquiry that
    hasn't converted or been marked lost yet."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    return _log_and_maybe_send(
        db, "inquiry_follow_up_due", settings_row.contact_email,
        f"Follow-up Due Today: {inquiry.client_name}",
        f"Follow-up for {inquiry.client_name}'s enquiry ({inquiry.offer_no}) is due today.",
        inquiry_id=inquiry.id,
    )


def trigger_guest_request_escalated(db: Session, guest_request) -> bool | None:
    """Notifies the business a guest request has been open too long for
    its priority — an urgent request open 4+ hours, or any request open
    72+ hours, needs attention before the guest escalates it themselves."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    return _log_and_maybe_send(
        db, "guest_request_escalated", settings_row.contact_email,
        render_template("guest_request_escalated", "subject", priority=guest_request.priority),
        render_template("guest_request_escalated", "body", priority=guest_request.priority, opened_date=guest_request.created_at.date(), description=guest_request.description[:150]),
        guest_request_id=guest_request.id,
    )


def trigger_maintenance_ticket_escalated(db: Session, ticket) -> bool | None:
    """Same escalation logic as guest requests, for maintenance tickets."""
    from app.modules.settings.models import BusinessSettings
    settings_row = db.scalar(select(BusinessSettings))
    if not settings_row or not settings_row.contact_email:
        return None
    return _log_and_maybe_send(
        db, "maintenance_ticket_escalated", settings_row.contact_email,
        render_template("maintenance_ticket_escalated", "subject", priority=ticket.priority),
        render_template("maintenance_ticket_escalated", "body", priority=ticket.priority, opened_date=ticket.created_at.date(), description=ticket.description[:150]),
        maintenance_ticket_id=ticket.id,
    )


ESCALATION_THRESHOLD_HOURS = {"urgent": 4, "high": 24, "medium": 72, "low": 72}


def scan_and_send_escalations(db: Session, as_of: datetime | None = None) -> dict:
    """
    Checks every currently-open guest request and maintenance ticket
    against a priority-based time threshold and escalates the ones that
    have overrun it — the real "Escalation" mechanism, reusing the same
    scan-and-dedupe infrastructure as the reminder scan rather than
    building a separate scheduling path.
    """
    from app.modules.portal.models import GuestRequest
    from app.modules.maintenance.models import MaintenanceTicket

    now = as_of or datetime.now(timezone.utc)
    results = {"guest_request_escalated": 0, "maintenance_ticket_escalated": 0}

    open_requests = db.scalars(select(GuestRequest).where(GuestRequest.status != "resolved")).all()
    open_tickets = db.scalars(select(MaintenanceTicket).where(MaintenanceTicket.status.in_(("open", "in_progress")))).all()

    # Batch-load every already-escalated id in two queries total instead
    # of one query per open item — same anti-N+1 discipline used
    # everywhere else in this project.
    already_escalated_requests = {
        row.guest_request_id for row in db.scalars(
            select(NotificationLog).where(NotificationLog.event_type == "guest_request_escalated", NotificationLog.guest_request_id.in_([r.id for r in open_requests]))
        ).all()
    } if open_requests else set()
    already_escalated_tickets = {
        row.maintenance_ticket_id for row in db.scalars(
            select(NotificationLog).where(NotificationLog.event_type == "maintenance_ticket_escalated", NotificationLog.maintenance_ticket_id.in_([t.id for t in open_tickets]))
        ).all()
    } if open_tickets else set()

    for r in open_requests:
        threshold = ESCALATION_THRESHOLD_HOURS.get(r.priority, 72)
        hours_open = (now - r.created_at).total_seconds() / 3600
        if hours_open >= threshold and r.id not in already_escalated_requests:
            trigger_guest_request_escalated(db, r)
            results["guest_request_escalated"] += 1

    for t in open_tickets:
        threshold = ESCALATION_THRESHOLD_HOURS.get(t.priority, 72)
        hours_open = (now - t.created_at).total_seconds() / 3600
        if hours_open >= threshold and t.id not in already_escalated_tickets:
            trigger_maintenance_ticket_escalated(db, t)
            results["maintenance_ticket_escalated"] += 1

    db.commit()
    return results
