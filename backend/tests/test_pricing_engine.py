"""
Pure unit tests for the pricing engine (no DB, no HTTP).
Run with: pytest tests/test_pricing_engine.py -v
"""
from datetime import date

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
