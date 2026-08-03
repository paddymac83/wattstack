"""Synthetic data only -- these test the plotting functions
themselves, with zero dependency on either real API."""
from datetime import datetime, timedelta

from wattstack_ingestion.plots import (
    overlay_two_series,
    price_by_hour_of_day,
    price_by_weekday,
    price_distribution,
    price_timeseries,
)


def _sample_series(n=48 * 7):
    start = datetime(2026, 1, 5)  # a Monday
    timestamps = [start + timedelta(minutes=30 * i) for i in range(n)]
    values = [50 + 20 * ((i % 48) / 48) for i in range(n)]
    return timestamps, values


def test_price_timeseries_builds_without_error():
    ts, vals = _sample_series()
    fig = price_timeseries(ts, vals, "Test", "GBP/MWh")
    assert len(fig.data) == 1
    assert list(fig.data[0].y) == vals


def test_price_distribution_builds_without_error():
    _, vals = _sample_series()
    fig = price_distribution(vals, "Test", "GBP/MWh")
    assert len(fig.data) == 1


def test_price_by_hour_of_day_has_one_box_per_populated_hour():
    ts, vals = _sample_series()
    fig = price_by_hour_of_day(ts, vals, "Test", "GBP/MWh")
    assert len(fig.data) == 24  # every hour has data across a full week of half-hours


def test_price_by_weekday_has_one_box_per_populated_day():
    ts, vals = _sample_series()
    fig = price_by_weekday(ts, vals, "Test", "GBP/MWh")
    assert len(fig.data) == 7  # a full week


def test_overlay_two_series_has_two_named_traces():
    ts, vals = _sample_series()
    fig = overlay_two_series(ts, vals, [v * 0.5 for v in vals], "Wholesale", "DC", "Test", "GBP")
    names = {t.name for t in fig.data}
    assert names == {"Wholesale", "DC"}
