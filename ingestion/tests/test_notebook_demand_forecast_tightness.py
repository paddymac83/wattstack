"""Smoke test for notebooks/demand_forecast_vs_system_tightness.py --
the important thing this proves, beyond the usual structural checks,
is that the day-ahead trigger vintage mechanism actually works: each
day's forecast is fetched as_of() exactly 10:00 UTC the day before,
not "now" and not some other offset.
"""
import runpy
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "demand_forecast_vs_system_tightness.py"
CACHE_PATH = Path("wattstack_ingestion_cache.sqlite")


class _FakeValue:
    def __init__(self, v):
        self.value = v


@pytest.fixture(autouse=True)
def _isolated_cache():
    CACHE_PATH.unlink(missing_ok=True)
    yield
    CACHE_PATH.unlink(missing_ok=True)


def _load_app():
    mod = runpy.run_path(str(NOTEBOOK_PATH), run_name="not_main")
    return mod["app"]


def test_notebook_cell_graph_resolves_without_error():
    app = _load_app()
    _, namespace = app.run()
    assert namespace is not None


def test_fetch_button_defaults_unclicked_and_gated_cells_are_absent():
    app = _load_app()
    _, namespace = app.run()
    assert namespace["fetch"].value is False
    keys = set(namespace.keys())
    assert "forecast_df" not in keys
    assert "joined_df" not in keys


_CALL_LOG: list[str] = []


def _fake_get(url, params=None, timeout=30):
    _CALL_LOG.append(url)
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "forecast/demand/day-ahead/history" in url:
        resp.json.return_value = [
            {"settlementPeriod": 1, "demand": 28000},
            {"settlementPeriod": 2, "demand": 32000},
        ]
    elif "system-prices" in url:
        day = url.rsplit("/", 1)[-1]
        resp.json.return_value = [
            {"settlementDate": day, "settlementPeriod": 1, "systemSellPrice": 40.0,
             "systemBuyPrice": 40.0, "netImbalanceVolume": -100.0},   # Long
            {"settlementDate": day, "settlementPeriod": 2, "systemSellPrice": 90.0,
             "systemBuyPrice": 90.0, "netImbalanceVolume": 200.0},    # Short
        ]
    else:
        resp.json.return_value = []
    return resp


def _run_happy_path(start=date(2026, 6, 1), days=2):
    _CALL_LOG.clear()
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "start_date": _FakeValue(start), "days_to_fetch": _FakeValue(days),
            "forecast_period_field": _FakeValue("settlementPeriod"), "demand_field": _FakeValue("demand"),
            "price_field": _FakeValue("systemSellPrice"), "niv_field": _FakeValue("netImbalanceVolume"),
            "bin_width": _FakeValue(5000),
        })
        return namespace


def test_trigger_time_is_exactly_1000_utc_the_day_before():
    """The core design property of this notebook: vintage, not
    'latest'. Wrong by even an hour would defeat the point."""
    _run_happy_path(start=date(2026, 6, 1), days=1)
    history_calls = [u for u in _CALL_LOG if "history" in u]
    assert len(history_calls) == 1
    assert "publishTime=2026-05-31T10:00:00+00:00" in history_calls[0]


def test_trigger_time_advances_correctly_across_multiple_days():
    _run_happy_path(start=date(2026, 6, 1), days=3)
    history_calls = sorted(u for u in _CALL_LOG if "history" in u)
    assert "publishTime=2026-05-31T10:00:00+00:00" in history_calls[0]
    assert "publishTime=2026-06-01T10:00:00+00:00" in history_calls[1]
    assert "publishTime=2026-06-02T10:00:00+00:00" in history_calls[2]


def test_join_matches_forecast_to_actual_by_day_and_period():
    namespace = _run_happy_path(days=1)
    df = namespace["joined_df"]
    assert len(df) == 2
    period_1 = df[df.settlement_period == 1].iloc[0]
    assert period_1["forecast_demand"] == 28000
    assert period_1["actual_price"] == 40.0
    assert period_1["system_length"] == "Long"


def test_higher_demand_period_correctly_classified_short_in_mock_data():
    namespace = _run_happy_path(days=1)
    df = namespace["joined_df"]
    period_2 = df[df.settlement_period == 2].iloc[0]
    assert period_2["forecast_demand"] == 32000
    assert period_2["system_length"] == "Short"


def test_two_real_calls_per_day_not_more():
    _run_happy_path(days=3)
    assert len(_CALL_LOG) == 6  # 3 days x (1 forecast history call + 1 system-prices call)
