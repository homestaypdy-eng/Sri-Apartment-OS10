"""Explicit, verified CustomerAccount -> booking Guest association.

Names are used only when creating a new Guest profile. Existing profiles are
matched solely through contact channels whose control was already verified
by customer-auth OTP (or by a verified Google email assertion).
"""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.bookings.models import Guest
from app.modules.customer_auth.models import CustomerAccount
from app.modules.portal.auth_service import normalize_email, normalize_phone


@dataclass(frozen=True)
class CustomerGuestResolution:
    guest: Guest
    created: bool
    linked_existing: bool


def resolve_customer_guest(db: Session, customer: CustomerAccount) -> CustomerGuestResolution:
    """Return the customer's stable Guest, linking only on verified evidence.

    The CustomerAccount row is locked to serialize first-time resolution for
    that account. Distinct verified contacts are collected as a set of Guest
    IDs; multiple matches are rejected instead of choosing an arbitrary row.
    If none match, a new Guest is created from the authenticated account's
    data. The Guest model requires a phone, so accounts without one must add
    and verify a phone before this flow can create a profile.
    """
    account = db.scalar(
        select(CustomerAccount).where(CustomerAccount.id == customer.id).with_for_update()
    )
    if account is None:
        raise HTTPException(401, "Customer account not found")

    if account.guest_id is not None:
        guest = db.get(Guest, account.guest_id)
        if guest is None:
            raise HTTPException(409, "Your guest profile needs staff assistance")
        if guest.merged_into_guest_id is not None:
            canonical = db.get(Guest, guest.merged_into_guest_id)
            if canonical is None or canonical.merged_into_guest_id is not None:
                raise HTTPException(409, "Your guest profile needs staff assistance")
            account.guest_id = canonical.id
            return CustomerGuestResolution(canonical, created=False, linked_existing=True)
        return CustomerGuestResolution(guest, created=False, linked_existing=False)

    verified_channels = []
    unverified_channels = []
    if account.email_verified_at is not None:
        verified_channels.append(("email", normalize_email(account.email)))
    else:
        unverified_channels.append(("email", normalize_email(account.email)))
    if account.phone_verified_at is not None and account.phone:
        verified_channels.append(("phone", normalize_phone(account.phone)))
    elif account.phone:
        unverified_channels.append(("phone", normalize_phone(account.phone)))
    if not verified_channels:
        raise HTTPException(409, "Verify your email or phone number before setting up identity verification")

    candidate_ids: set[object] = set()
    for channel, identifier in verified_channels:
        if channel == "email":
            matches = db.scalars(
                select(Guest).where(
                    Guest.email == identifier,
                    Guest.merged_into_guest_id.is_(None),
                )
            ).all()
        else:
            matches = db.scalars(
                select(Guest).where(
                    Guest.phone == identifier,
                    Guest.merged_into_guest_id.is_(None),
                )
            ).all()
        candidate_ids.update(guest.id for guest in matches)

    if len(candidate_ids) > 1:
        raise HTTPException(409, "We could not safely match your account to one guest profile. Please contact Sri Apartment for assistance")

    # An unverified contact is never ownership evidence. Still detect when
    # it points at a DIFFERENT existing Guest so we don't create/link a
    # duplicate profile that later makes phone-based booking reuse
    # ambiguous. The customer must verify that channel or ask staff to
    # resolve the records.
    for channel, identifier in unverified_channels:
        if channel == "email":
            matches = db.scalars(select(Guest.id).where(
                Guest.email == identifier, Guest.merged_into_guest_id.is_(None),
            )).all()
        else:
            matches = db.scalars(select(Guest.id).where(
                Guest.phone == identifier, Guest.merged_into_guest_id.is_(None),
            )).all()
        if any(guest_id not in candidate_ids for guest_id in matches):
            raise HTTPException(409, "Please verify your matching contact method or contact Sri Apartment to link your guest profile safely")

    if candidate_ids:
        guest = db.get(Guest, next(iter(candidate_ids)))
        assert guest is not None
        account.guest_id = guest.id
        return CustomerGuestResolution(guest, created=False, linked_existing=True)

    if not account.phone:
        raise HTTPException(409, "Add a phone number to your account before setting up identity verification")

    guest = Guest(
        name=f"{account.first_name} {account.last_name}".strip(),
        phone=normalize_phone(account.phone),
        email=account.email,
    )
    db.add(guest)
    db.flush()
    account.guest_id = guest.id
    return CustomerGuestResolution(guest, created=True, linked_existing=False)
