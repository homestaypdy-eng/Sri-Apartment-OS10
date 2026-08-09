"""
Dynamic pricing engine.

Resolution order per night, from highest to lowest priority:
  1. Seasonal rate override (if the night falls within any SeasonalRate
     range for this unit) — if multiple seasonal ranges overlap for the
     same night, the most-recently-created one wins (documented choice,
     not a silent/undefined one).
  2. Weekend rate, if the night is a Friday or Saturday (the night being
     stayed) and a weekend_rate is set on the unit.
  3. The unit's base_rate (weekday rate) otherwise.

Long-stay discount is then applied as a percentage off the summed
pre-discount subtotal, using the tier with the HIGHEST min_nights the
stay still qualifies for (see LongStayDiscountTier docstring in models.py).

This module is deliberately pure/stateless (takes plain data in, returns
plain data out) so it's easy to unit-test without a live database inside
the pricing math itself — the DB is only touched by the caller to fetch
the unit's rates/seasonal rates/discount tiers first.
"""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class DayRate:
    the_date: date
    rate: float
    source: str  # "seasonal" | "weekend" | "weekday"
    seasonal_name: str | None = None


@dataclass
class PriceQuote:
    check_in: date
    check_out: date
    nights: int
    daily_breakdown: list[DayRate]
    subtotal_before_discount: float
    long_stay_discount_percent: float
    long_stay_discount_amount: float
    subtotal_after_discount: float
    average_nightly_rate: float  # subtotal_after_discount / nights — feeds GST slab determination


def _is_weekend_night(the_date: date) -> bool:
    """Friday (4) or Saturday (5) nights count as 'weekend' — the night
    before a non-work day, the standard hospitality convention (not
    calendar Saturday/Sunday)."""
    return the_date.weekday() in (4, 5)


def calculate_price(
    check_in: date,
    check_out: date,
    base_rate: float,
    weekend_rate: float | None,
    seasonal_rates: list[dict],
    discount_tiers: list[dict],
) -> PriceQuote:
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")

    nights = (check_out - check_in).days
    daily: list[DayRate] = []

    sorted_seasonal = sorted(seasonal_rates, key=lambda s: s.get("created_at") or date.min)

    current = check_in
    while current < check_out:
        matched_seasonal = None
        for s in sorted_seasonal:
            if s["start_date"] <= current <= s["end_date"]:
                matched_seasonal = s

        if matched_seasonal is not None:
            daily.append(DayRate(the_date=current, rate=float(matched_seasonal["rate_per_night"]), source="seasonal", seasonal_name=matched_seasonal["name"]))
        elif _is_weekend_night(current) and weekend_rate is not None:
            daily.append(DayRate(the_date=current, rate=float(weekend_rate), source="weekend"))
        else:
            daily.append(DayRate(the_date=current, rate=float(base_rate), source="weekday"))

        current += timedelta(days=1)

    subtotal_before_discount = round(sum(d.rate for d in daily), 2)

    applicable_tiers = [t for t in discount_tiers if nights >= t["min_nights"]]
    best_tier = max(applicable_tiers, key=lambda t: t["min_nights"]) if applicable_tiers else None
    discount_percent = float(best_tier["discount_percent"]) if best_tier else 0.0
    discount_amount = round(subtotal_before_discount * discount_percent / 100, 2)
    subtotal_after_discount = round(subtotal_before_discount - discount_amount, 2)
    average_nightly_rate = round(subtotal_after_discount / nights, 2) if nights else 0.0

    return PriceQuote(
        check_in=check_in,
        check_out=check_out,
        nights=nights,
        daily_breakdown=daily,
        subtotal_before_discount=subtotal_before_discount,
        long_stay_discount_percent=discount_percent,
        long_stay_discount_amount=discount_amount,
        subtotal_after_discount=subtotal_after_discount,
        average_nightly_rate=average_nightly_rate,
    )
