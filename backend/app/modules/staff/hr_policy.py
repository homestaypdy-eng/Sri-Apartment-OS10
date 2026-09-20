"""
Pure, stateless HR policy resolution and calculation for the Staff module —
same convention as properties/pricing.py: this module never touches the
database or a session; callers fetch rows and pass them in, so every
function here is trivially unit-testable.
"""
import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass
class ResolvedPolicy:
    """
    The policy that actually applies to one employee right now. An
    employee's own *_override field on StaffProfile wins whenever it is
    set (not None); otherwise the linked StaffDesignation's default
    applies; otherwise the field is None ("unconfigured") — this never
    invents a fallback number for a profile with neither an override nor
    a linked designation.
    """
    basic_salary: float | None
    shift_start: time | None
    shift_end: time | None
    lunch_break_minutes: int | None
    standard_working_hours: float | None
    leave_entitlement_per_month: int | None
    overtime_mode: str | None
    overtime_rate_multiplier: float | None
    source: dict[str, str]  # per-field: "override" | "designation" | "unconfigured" — for UI display


def _pick(override_value, default_value):
    if override_value is not None:
        return override_value, "override"
    if default_value is not None:
        return default_value, "designation"
    return None, "unconfigured"


def resolve_employee_policy(profile, designation) -> ResolvedPolicy:
    """profile: StaffProfile row. designation: StaffDesignation row or None
    (e.g. profile.designation_id is unset, or was never linked)."""
    salary, salary_src = _pick(profile.salary_override, designation.default_basic_salary if designation else None)
    shift_start, shift_start_src = _pick(profile.shift_start_override, designation.default_shift_start if designation else None)
    shift_end, shift_end_src = _pick(profile.shift_end_override, designation.default_shift_end if designation else None)
    lunch, lunch_src = _pick(profile.lunch_break_override_minutes, designation.default_lunch_break_minutes if designation else None)
    hours, hours_src = _pick(
        profile.standard_working_hours_override,
        float(designation.standard_working_hours) if designation else None,
    )
    leave, leave_src = _pick(profile.leave_entitlement_override, designation.leave_entitlement_per_month if designation else None)

    return ResolvedPolicy(
        basic_salary=float(salary) if salary is not None else None,
        shift_start=shift_start,
        shift_end=shift_end,
        lunch_break_minutes=lunch,
        standard_working_hours=float(hours) if hours is not None else None,
        leave_entitlement_per_month=leave,
        overtime_mode=designation.overtime_mode if designation else None,
        overtime_rate_multiplier=(
            float(designation.overtime_rate_multiplier)
            if designation and designation.overtime_rate_multiplier is not None
            else None
        ),
        source={
            "basic_salary": salary_src,
            "shift_start": shift_start_src,
            "shift_end": shift_end_src,
            "lunch_break_minutes": lunch_src,
            "standard_working_hours": hours_src,
            "leave_entitlement_per_month": leave_src,
        },
    )


@dataclass
class AttendanceComputation:
    total_presence_minutes: int
    lunch_break_minutes_applied: int
    working_minutes: int
    overtime_minutes: int


def compute_attendance_hours(
    attendance_date: date,
    check_in_time: time,
    check_out_time: time,
    lunch_break_minutes: int | None,
    standard_working_hours: float | None,
) -> AttendanceComputation:
    """
    Total presence = check-out − check-in, computed on real datetimes (not
    bare Time subtraction) so overnight shifts work: if check-out's clock
    time is not after check-in's, the shift is assumed to have crossed
    midnight and check-out is treated as falling on the next calendar day.
    This is a heuristic given check_in_time/check_out_time are bare Time
    columns with no independent date-crossing flag — a precise fix would
    store real timestamps, which is a larger, separate schema change.

    Working minutes and overtime are both clamped at zero: a short
    attendance record never produces negative working minutes, and
    falling short of standard hours is a shortfall, not "negative
    overtime" — it is not represented as its own field in this phase.
    """
    check_in_dt = datetime.combine(attendance_date, check_in_time)
    check_out_dt = datetime.combine(attendance_date, check_out_time)
    if check_out_dt <= check_in_dt:
        check_out_dt += timedelta(days=1)

    total_presence_minutes = int((check_out_dt - check_in_dt).total_seconds() // 60)
    lunch_applied = max(0, lunch_break_minutes or 0)
    working_minutes = max(0, total_presence_minutes - lunch_applied)
    standard_minutes = int(round((standard_working_hours or 0) * 60))
    overtime_minutes = max(0, working_minutes - standard_minutes)

    return AttendanceComputation(
        total_presence_minutes=total_presence_minutes,
        lunch_break_minutes_applied=lunch_applied,
        working_minutes=working_minutes,
        overtime_minutes=overtime_minutes,
    )


def days_in_month_range(leave_from: date, leave_to: date, year: int, month: int) -> int:
    """
    Number of days of the inclusive range [leave_from, leave_to] that fall
    inside the given calendar month — a leave request spanning a month
    boundary is only counted for the days actually inside that month, on
    whichever side you ask about.
    """
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    start = max(leave_from, month_start)
    end = min(leave_to, month_end)
    if end < start:
        return 0
    return (end - start).days + 1


def count_approved_leave_days_in_month(leave_requests, year: int, month: int) -> int:
    """Sum of days_in_month_range(...) across all *approved* leave requests
    (pending/rejected are excluded)."""
    return sum(
        days_in_month_range(lr.from_date, lr.to_date, year, month)
        for lr in leave_requests
        if lr.status == "approved"
    )


def split_free_and_excess_leave(approved_days_this_month: int, leave_entitlement: int | None) -> tuple[int, int]:
    """
    Returns (free_days, excess_days). The first `leave_entitlement`
    approved days in the month are free (zero salary impact). This
    function only counts how many days are in excess — it deliberately
    does NOT compute a deduction amount, since no per-day deduction rate
    has been defined; see the plan's §F/§J for why that's left manual.
    """
    entitlement = leave_entitlement if leave_entitlement is not None else 0
    free_days = min(approved_days_this_month, entitlement)
    excess_days = max(0, approved_days_this_month - entitlement)
    return free_days, excess_days
