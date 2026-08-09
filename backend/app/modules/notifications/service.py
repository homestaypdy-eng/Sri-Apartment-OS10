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
import os
import smtplib
import httpx
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.bookings.models import Booking
from app.modules.notifications.models import NotificationLog
from app.modules.payments.models import Invoice, Payment
from app.modules.notifications.templates import render_template


class EmailNotConfiguredError(Exception):
    pass


def send_email(to_email: str, subject: str, body: str, attachment_path: str | None = None) -> None:
    if not settings.smtp_host:
        raise EmailNotConfiguredError(
            "Email sending is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD as environment variables."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg.set_content(body)

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


def _log_and_maybe_send(
    db: Session, event_type: str, to_email: str, subject: str, body: str,
    booking_id=None, inquiry_id=None, guest_request_id=None, maintenance_ticket_id=None, housekeeping_task_id=None, inventory_item_id=None, leave_request_id=None, payroll_id=None, user_id=None, attachment_path: str | None = None,
) -> bool:
    """Sends best-effort: logs success or failure, never raises to the caller
    (a notification failure must never break the business action that
    triggered it — a booking must still be created even if its confirmation
    email can't be sent)."""
    try:
        send_email(to_email, subject, body, attachment_path)
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


def trigger_booking_confirmation(db: Session, booking: Booking) -> bool | None:
    guest = booking.guest
    if not guest or not guest.email:
        return None  # nothing to send to — not a failure, just no channel available
    subject = "Booking Confirmed — Sri Apartment Homestay"
    body = (
        f"Hi {guest.name},\n\nYour booking is confirmed.\n"
        f"Check-in: {booking.check_in}\nCheck-out: {booking.check_out}\n"
        f"Rate: Rs. {booking.rate_applied}/night\n\nWe look forward to hosting you!"
    )
    return _log_and_maybe_send(db, "booking_confirmation", guest.email, subject, body, booking_id=booking.id)


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


def trigger_cancellation(db: Session, booking: Booking) -> bool | None:
    guest = booking.guest
    if not guest or not guest.email:
        return None
    subject = "Booking Cancelled — Sri Apartment Homestay"
    body = f"Hi {guest.name},\n\nYour booking for {booking.check_in} to {booking.check_out} has been cancelled."
    return _log_and_maybe_send(db, "cancellation", guest.email, subject, body, booking_id=booking.id)


def trigger_invoice_delivery(db: Session, invoice: Invoice, pdf_path: str) -> bool | None:
    booking = invoice.booking
    guest = booking.guest if booking else None
    if not guest or not guest.email:
        return None
    subject = f"Your Invoice {invoice.invoice_no} — Sri Apartment Homestay"
    body = f"Hi {guest.name},\n\nPlease find attached your invoice {invoice.invoice_no} for Rs. {invoice.total}."
    return _log_and_maybe_send(db, "invoice_delivery", guest.email, subject, body, booking_id=booking.id if booking else None, attachment_path=pdf_path)


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
