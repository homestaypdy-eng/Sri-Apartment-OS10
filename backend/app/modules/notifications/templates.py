"""
Centralized notification message templates.

Rather than refactor every existing trigger function in service.py (many
of which are already tested and certified across multiple modules — a
mass refactor for no functional benefit is exactly the kind of
unrequested rewrite this project's completion passes have deliberately
avoided), this module gives new and future notification code a single
place to define message text instead of scattering it as inline
f-strings. Applied here to the notifications actually built in this
pass (SMS booking confirmation, escalations) as real, working proof —
not a parallel system nobody uses.

Each template is a plain Python format string; render_template does the
substitution and raises a clear error if a required placeholder is
missing, rather than silently sending a broken message.
"""

TEMPLATES: dict[str, dict[str, str]] = {
    "booking_confirmation_sms": {
        "body": (
            "Sri Apartment Homestay: Booking confirmed for {check_in} to {check_out}. "
            "Rate Rs.{rate}/night. We look forward to hosting you!"
        ),
    },
    "guest_request_escalated": {
        "subject": "ESCALATED: Guest Request Still Open ({priority})",
        "body": (
            "A {priority}-priority guest request has been open since {opened_date} "
            "and still isn't resolved: {description}"
        ),
    },
    "maintenance_ticket_escalated": {
        "subject": "ESCALATED: Maintenance Ticket Still Open ({priority})",
        "body": (
            "A {priority}-priority maintenance ticket has been open since {opened_date} "
            "and still isn't resolved: {description}"
        ),
    },
    "invoice_delivery": {
        "subject": "Your Invoice {invoice_no} — {property_name}",
        "body": (
            "Hi {guest_name},\n\n"
            "Thank you for booking with {property_name}. Here are your booking and invoice details:\n\n"
            "Booking reference: {booking_id}\n"
            "Property: {property_name}\n"
            "Accommodation: {unit_name}\n"
            "Check-in: {check_in}{arrival_time_suffix}\n"
            "Check-out: {check_out}{departure_time_suffix}\n"
            "Nights: {nights}\n"
            "Guests: {guests}\n"
            "Amount paid: Rs. {amount_paid}\n"
            "Payment status: {payment_status}\n"
            "Invoice number: {invoice_no}\n\n"
            "Your invoice is attached to this email as a PDF.\n\n"
            "We look forward to hosting you!"
        ),
    },
}


def render_template(template_key: str, field: str, **kwargs) -> str:
    """Renders one field (e.g. 'subject' or 'body') of a named template
    with the given placeholders. Raises a clear error naming the missing
    placeholder rather than letting a half-rendered message go out."""
    template = TEMPLATES.get(template_key)
    if template is None:
        raise ValueError(f"No template registered for '{template_key}'")
    raw = template.get(field)
    if raw is None:
        raise ValueError(f"Template '{template_key}' has no '{field}' field")
    try:
        return raw.format(**kwargs)
    except KeyError as e:
        raise ValueError(f"Template '{template_key}.{field}' is missing placeholder {e}") from e
