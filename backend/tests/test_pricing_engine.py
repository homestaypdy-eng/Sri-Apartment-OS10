"""
Pure unit tests for the pricing engine (no DB, no HTTP).
Run with: pytest tests/test_pricing_engine.py -v
"""
from datetime import date, timedelta

import pytest

from app.modules.properties.pricing import calculate_price


def test_all_weekday_nights_use_base_rate():
    quote = calculate_price(date(2026, 1, 5), date(2026, 1, 8), base_rate=2000, weekend_rate=None, seasonal_rates=[], discount_tiers=[])
    assert quote.nights == 3
    assert all(d.source == "weekday" and d.rate == 2000 for d in quote.daily_breakdown)
    assert quote.subtotal_before_discount == 6000.0
    assert quote.subtotal_after_discount == 6000.0


def test_weekend_rate_applied_to_friday_and_saturday_nights():
    quote = calculate_price(date(2026, 1, 8), date(2026, 1, 11), base_rate=2000, weekend_rate=3000, seasonal_rates=[], discount_tiers=[])
    sources = {d.the_date.isoformat(): d.source for d in quote.daily_breakdown}
    assert sources[date(2026, 1, 8).isoformat()] == "weekday"
    assert sources[date(2026, 1, 9).isoformat()] == "weekend"
    assert sources[date(2026, 1, 10).isoformat()] == "weekend"
    assert quote.subtotal_before_discount == 2000 + 3000 + 3000


def test_no_weekend_rate_set_falls_back_to_base_rate_on_weekend_nights():
    quote = calculate_price(date(2026, 1, 9), date(2026, 1, 10), base_rate=2000, weekend_rate=None, seasonal_rates=[], discount_tiers=[])
    assert quote.daily_breakdown[0].source == "weekday"
    assert quote.daily_breakdown[0].rate == 2000


def test_seasonal_rate_overrides_weekend_rate():
    seasonal = [{"id": "s1", "name": "Diwali", "start_date": date(2026, 1, 9), "end_date": date(2026, 1, 9), "rate_per_night": 5000, "created_at": date(2026, 1, 1)}]
    quote = calculate_price(date(2026, 1, 9), date(2026, 1, 10), base_rate=2000, weekend_rate=3000, seasonal_rates=seasonal, discount_tiers=[])
    assert quote.daily_breakdown[0].source == "seasonal"
    assert quote.daily_breakdown[0].rate == 5000
    assert quote.daily_breakdown[0].seasonal_name == "Diwali"


def test_overlapping_seasonal_rates_most_recently_created_wins():
    older = {"id": "s1", "name": "Old Promo", "start_date": date(2026, 2, 1), "end_date": date(2026, 2, 10), "rate_per_night": 4000, "created_at": date(2026, 1, 1)}
    newer = {"id": "s2", "name": "New Promo", "start_date": date(2026, 2, 5), "end_date": date(2026, 2, 8), "rate_per_night": 6000, "created_at": date(2026, 1, 15)}
    quote = calculate_price(date(2026, 2, 5), date(2026, 2, 6), base_rate=2000, weekend_rate=None, seasonal_rates=[older, newer], discount_tiers=[])
    assert quote.daily_breakdown[0].rate == 6000
    assert quote.daily_breakdown[0].seasonal_name == "New Promo"


def test_long_stay_discount_picks_highest_qualifying_tier():
    tiers = [{"min_nights": 3, "discount_percent": 5}, {"min_nights": 7, "discount_percent": 10}, {"min_nights": 30, "discount_percent": 20}]
    quote = calculate_price(date(2026, 3, 1), date(2026, 3, 11), base_rate=1000, weekend_rate=None, seasonal_rates=[], discount_tiers=tiers)
    assert quote.nights == 10
    assert quote.subtotal_before_discount == 10000.0
    assert quote.long_stay_discount_percent == 10.0
    assert quote.long_stay_discount_amount == 1000.0
    assert quote.subtotal_after_discount == 9000.0


def test_stay_too_short_for_any_discount_tier_gets_zero_discount():
    tiers = [{"min_nights": 7, "discount_percent": 10}]
    quote = calculate_price(date(2026, 3, 1), date(2026, 3, 3), base_rate=1000, weekend_rate=None, seasonal_rates=[], discount_tiers=tiers)
    assert quote.nights == 2
    assert quote.long_stay_discount_percent == 0.0
    assert quote.subtotal_after_discount == quote.subtotal_before_discount


def test_average_nightly_rate_is_post_discount():
    tiers = [{"min_nights": 5, "discount_percent": 10}]
    quote = calculate_price(date(2026, 4, 1), date(2026, 4, 6), base_rate=2000, weekend_rate=None, seasonal_rates=[], discount_tiers=tiers)
    assert quote.subtotal_after_discount == 9000.0
    assert quote.average_nightly_rate == 1800.0


def test_checkout_before_checkin_raises():
    with pytest.raises(ValueError):
        calculate_price(date(2026, 1, 10), date(2026, 1, 5), base_rate=1000, weekend_rate=None, seasonal_rates=[], discount_tiers=[])


# ---------------------------------------------------------------------------
# Long-stay absolute nightly rate (Rs.2,000/night at 30+ nights, business rule)
# ---------------------------------------------------------------------------

LONG_STAY_MIN_NIGHTS = 30
LONG_STAY_RATE = 2000
STANDARD_BASE_RATE = 3500
STANDARD_WEEKEND_RATE = 3800


def _long_stay_quote(nights, check_in=date(2026, 11, 2)):  # 2026-11-02 is a Monday
    return calculate_price(
        check_in, check_in + timedelta(days=nights),
        base_rate=STANDARD_BASE_RATE, weekend_rate=STANDARD_WEEKEND_RATE,
        seasonal_rates=[], discount_tiers=[],
        long_stay_min_nights=LONG_STAY_MIN_NIGHTS, long_stay_rate=LONG_STAY_RATE,
    )


def test_example_a_29_nights_not_eligible_uses_standard_pricing():
    quote = _long_stay_quote(29)
    assert quote.nights == 29
    assert "long_stay" not in {d.source for d in quote.daily_breakdown}
    assert quote.average_nightly_rate != LONG_STAY_RATE


def test_example_b_30_nights_exactly_60000():
    quote = _long_stay_quote(30)
    assert quote.nights == 30
    assert quote.subtotal_before_discount == 60000.0
    assert quote.subtotal_after_discount == 60000.0
    assert quote.average_nightly_rate == 2000.0
    assert quote.long_stay_discount_percent == 0.0
    assert all(d.source == "long_stay" and d.rate == 2000.0 for d in quote.daily_breakdown)


def test_example_c_31_nights_exactly_62000():
    quote = _long_stay_quote(31)
    assert quote.subtotal_before_discount == 62000.0


def test_example_d_60_nights_exactly_120000():
    quote = _long_stay_quote(60)
    assert quote.subtotal_before_discount == 120000.0


def test_long_stay_rate_is_absolute_not_a_percentage_of_standard_pricing():
    """The critical distinction from LongStayDiscountTier: this is a flat
    override, not a discount off the weekday/weekend subtotal. Prove the
    30-night result is NOT what a discount-off-standard-pricing would give."""
    quote = _long_stay_quote(30)
    # What 30 nights of standard weekday/weekend pricing (no long-stay rule)
    # would have cost, for contrast:
    standard = calculate_price(
        date(2026, 11, 2), date(2026, 12, 2),
        base_rate=STANDARD_BASE_RATE, weekend_rate=STANDARD_WEEKEND_RATE,
        seasonal_rates=[], discount_tiers=[],
    )
    assert standard.subtotal_before_discount != 60000.0  # sanity: standard pricing is a different number
    assert quote.subtotal_before_discount == 60000.0  # long-stay rate ignores it entirely


def test_long_stay_booking_crossing_multiple_weekends_still_flat_rate():
    """A 30-night stay starting on a Friday crosses 4 weekends — must still
    be flat Rs.2,000/night throughout, never mixed with the Rs.3,800
    weekend rate."""
    quote = _long_stay_quote(30, check_in=date(2026, 11, 6))  # a Friday
    assert quote.nights == 30
    assert quote.subtotal_before_discount == 60000.0
    assert all(d.source == "long_stay" for d in quote.daily_breakdown)
    assert all(d.rate == 2000.0 for d in quote.daily_breakdown)


def test_long_stay_rate_takes_priority_over_percentage_discount_tier_never_stacks():
    """If a unit has BOTH the absolute long-stay rate AND an old-style
    percentage LongStayDiscountTier configured, the absolute rate wins
    completely — no percentage discount is additionally applied on top."""
    tiers = [{"min_nights": 30, "discount_percent": 20}]
    quote = calculate_price(
        date(2026, 11, 2), date(2026, 12, 2),  # 30 nights
        base_rate=STANDARD_BASE_RATE, weekend_rate=STANDARD_WEEKEND_RATE,
        seasonal_rates=[], discount_tiers=tiers,
        long_stay_min_nights=LONG_STAY_MIN_NIGHTS, long_stay_rate=LONG_STAY_RATE,
    )
    assert quote.subtotal_before_discount == 60000.0
    assert quote.subtotal_after_discount == 60000.0  # NOT 60000 * 0.8 = 48000
    assert quote.long_stay_discount_percent == 0.0


def test_percentage_tier_still_applies_when_stay_too_short_for_absolute_rate():
    """Below long_stay_min_nights, the OLD percentage-tier mechanism works
    exactly as before — unaffected by the new absolute-rate feature."""
    tiers = [{"min_nights": 7, "discount_percent": 10}]
    quote = calculate_price(
        date(2026, 11, 2), date(2026, 11, 12),  # 10 nights — below the 30-night absolute-rate threshold
        base_rate=STANDARD_BASE_RATE, weekend_rate=STANDARD_WEEKEND_RATE,
        seasonal_rates=[], discount_tiers=tiers,
        long_stay_min_nights=LONG_STAY_MIN_NIGHTS, long_stay_rate=LONG_STAY_RATE,
    )
    assert quote.long_stay_discount_percent == 10.0
    assert quote.subtotal_after_discount < quote.subtotal_before_discount


def test_long_stay_params_omitted_is_fully_backward_compatible():
    """Callers that don't pass long_stay_min_nights/long_stay_rate at all
    (old call sites, or tests written before this feature) get exactly the
    old behavior — no long-stay override is possible without both params."""
    quote = calculate_price(
        date(2026, 11, 2), date(2026, 12, 2),  # 30 nights
        base_rate=STANDARD_BASE_RATE, weekend_rate=STANDARD_WEEKEND_RATE,
        seasonal_rates=[], discount_tiers=[],
    )
    assert "long_stay" not in {d.source for d in quote.daily_breakdown}


def test_weekday_pricing_unaffected_by_long_stay_feature():
    quote = _long_stay_quote(1, check_in=date(2026, 11, 2))  # Monday, 1 night
    assert quote.subtotal_before_discount == 3500.0


def test_weekend_pricing_unaffected_by_long_stay_feature():
    quote = _long_stay_quote(1, check_in=date(2026, 11, 6))  # Friday, 1 night
    assert quote.subtotal_before_discount == 3800.0


def test_mixed_weekday_weekend_short_stay_unaffected_by_long_stay_feature():
    quote = _long_stay_quote(7, check_in=date(2026, 11, 2))  # Mon->Mon, includes Fri+Sat
    assert quote.nights == 7
    assert quote.subtotal_before_discount == 5 * 3500 + 2 * 3800  # 25100
