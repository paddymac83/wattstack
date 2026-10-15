"""Smoke test for notebooks/demand_forecast_vs_system_tightness.py --
the important thing this proves, beyond the usual structural checks,
is that the day-ahead trigger vintage mechanism actually works for
demand forecast (as_of() exactly 10:00 UTC the day before), and that
the LOLP/margin section -- a genuinely different fetch shape, one
bulk call instead of one per day -- joins correctly onto it.
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
    assert "lolp_df" not in keys
    assert "joined_with_lolp_df" not in keys
    assert "wind_df" not in keys
    assert "joined_with_wind_df" not in keys


_CALL_LOG: list[tuple[str, dict]] = []


def _fake_get(url, params=None, timeout=30):
    _CALL_LOG.append((url, params or {}))
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "forecast/demand/day-ahead/history" in url:
        resp.json.return_value = [
            {"settlementPeriod": 1, "demand": 28000},
            {"settlementPeriod": 2, "demand": 32000},
        ]
    elif "forecast/system/loss-of-load" in url:
        resp.json.return_value = [
            {"settlementDate": "2026-06-01T00:00:00", "settlementPeriod": 1, "lolp12hPlus": 0.01},
            {"settlementDate": "2026-06-01T00:00:00", "settlementPeriod": 2, "lolp12hPlus": 0.08},
            {"settlementDate": "2026-06-02T00:00:00", "settlementPeriod": 1, "lolp12hPlus": 0.02},
            {"settlementDate": "2026-06-02T00:00:00", "settlementPeriod": 2, "lolp12hPlus": 0.07},
            {"settlementDate": "2026-06-03T00:00:00", "settlementPeriod": 1, "lolp12hPlus": 0.015},
            {"settlementDate": "2026-06-03T00:00:00", "settlementPeriod": 2, "lolp12hPlus": 0.075},
        ]
    elif "forecast/generation/wind/history" in url:
        resp.json.return_value = [
            {"settlementPeriod": 1, "generation": 2000},   # low wind
            {"settlementPeriod": 2, "generation": 12000},  # high wind
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


def _run_happy_path(start=date(2026, 6, 1), days=2, with_lolp_fields=True, with_wind_fields=True):
    _CALL_LOG.clear()
    defs = {
        "fetch": _FakeValue(True), "start_date": _FakeValue(start), "days_to_fetch": _FakeValue(days),
        "forecast_period_field": _FakeValue("settlementPeriod"), "demand_field": _FakeValue("demand"),
        "price_field": _FakeValue("systemSellPrice"), "niv_field": _FakeValue("netImbalanceVolume"),
        "bin_width": _FakeValue(5000),
    }
    if with_lolp_fields:
        defs.update({
            "lolp_date_field": _FakeValue("settlementDate"), "lolp_period_field": _FakeValue("settlementPeriod"),
            "lolp_value_field": _FakeValue("lolp12hPlus"), "lolp_bin_width": _FakeValue(0.05),
        })
    if with_wind_fields:
        defs.update({
            "wind_period_field": _FakeValue("settlementPeriod"), "wind_value_field": _FakeValue("generation"),
            "wind_bin_width": _FakeValue(5000),
        })
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get):
        app = _load_app()
        _, namespace = app.run(defs=defs)
        return namespace


def test_trigger_time_is_exactly_1000_utc_the_day_before():
    """The core design property of the demand-forecast half of this
    notebook: vintage, not 'latest'. Wrong by even an hour would
    defeat the point."""
    _run_happy_path(start=date(2026, 6, 1), days=1)
    history_calls = [(u, p) for u, p in _CALL_LOG if "demand/day-ahead/history" in u]
    assert len(history_calls) == 1
    assert history_calls[0][1]["publishTime"] == "2026-05-31T10:00:00+00:00"


def test_trigger_time_advances_correctly_across_multiple_days():
    _run_happy_path(start=date(2026, 6, 1), days=3)
    history_calls = sorted(p["publishTime"] for u, p in _CALL_LOG if "demand/day-ahead/history" in u)
    assert history_calls == [
        "2026-05-31T10:00:00+00:00",
        "2026-06-01T10:00:00+00:00",
        "2026-06-02T10:00:00+00:00",
    ]


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


def test_no_plus_character_leaks_into_any_called_url():
    """The actual bug, checked directly against this notebook's real
    usage: every trigger_time and the LOLP from/to range are
    tz-aware datetimes, whose isoformat() contains a literal '+'.
    Confirms none of that ever ends up concatenated into a URL
    string (where it would silently mean 'space') -- it must only
    ever appear inside a params dict, where requests encodes it
    correctly."""
    _run_happy_path(start=date(2026, 6, 1), days=2)
    for url, _params in _CALL_LOG:
        assert "+" not in url


def test_calls_per_day_account_for_demand_wind_and_lolp_fetches():
    """3 per-day calls (demand history + system prices + wind
    history) x 3 days, plus exactly 1 bulk LOLP call for the whole
    window -- not 3."""
    _run_happy_path(days=3)
    assert len(_CALL_LOG) == 10


def test_lolp_fetch_is_a_single_bulk_call_not_per_day():
    _run_happy_path(start=date(2026, 6, 1), days=5)
    lolp_calls = [(u, p) for u, p in _CALL_LOG if "loss-of-load" in u]
    assert len(lolp_calls) == 1
    assert lolp_calls[0][1]["from"] == "2026-06-01T00:00:00+00:00"
    assert lolp_calls[0][1]["to"] == "2026-06-06T00:00:00+00:00"


def test_lolp_joins_correctly_onto_existing_demand_comparison():
    namespace = _run_happy_path(days=2)
    df = namespace["joined_with_lolp_df"]
    assert len(df) == 4  # 2 days x 2 periods, all matched
    row = df[(df.day == "2026-06-01") & (df.settlement_period == 2)].iloc[0]
    assert row["lolp_value"] == 0.08
    assert row["system_length"] == "Short"  # unchanged from the demand-only join


def test_lolp_date_field_with_time_component_is_correctly_truncated_for_matching():
    """The mock LOLP date field is a full datetime string
    ('2026-06-01T00:00:00'), not a bare date -- proves the [:10]
    truncation in the notebook actually works, not just that dates
    happen to match by luck."""
    namespace = _run_happy_path(days=1)
    df = namespace["joined_with_lolp_df"]
    assert len(df) == 2  # both periods matched despite the datetime-format date field


def test_gated_lolp_cells_absent_when_lolp_fields_not_yet_picked():
    """Demand-only analysis should still work even before the LOLP
    fields are chosen -- LOLP is additive, not a hard requirement."""
    namespace = _run_happy_path(days=1, with_lolp_fields=False)
    assert "joined_df" in namespace  # demand-only join still present
    assert "joined_with_lolp_df" not in namespace  # LOLP join correctly gated off


def test_wind_trigger_time_matches_the_same_day_ahead_convention_as_demand():
    """Wind genuinely fits ForecastProvider (unlike LOLP) -- it
    should use the identical 10:00 UTC day-before trigger, not a
    special-cased time tied to wind's own 8x/day publication
    schedule. The history endpoint resolves "what was known at
    10:00" to whichever real publication (likely 08:30) preceded it;
    the notebook doesn't need to know that schedule itself."""
    _run_happy_path(start=date(2026, 6, 1), days=1)
    wind_calls = [(u, p) for u, p in _CALL_LOG if "wind/history" in u]
    assert len(wind_calls) == 1
    assert wind_calls[0][1]["publishTime"] == "2026-05-31T10:00:00+00:00"


def test_wind_joins_correctly_onto_existing_demand_comparison():
    namespace = _run_happy_path(days=1)
    df = namespace["joined_with_wind_df"]
    assert len(df) == 2
    period_2 = df[df.settlement_period == 2].iloc[0]
    assert period_2["wind_forecast"] == 12000
    assert period_2["system_length"] == "Short"  # unchanged from the demand-only join


def test_wind_volatility_chart_distinguishes_low_and_high_spread_buckets():
    """The point of spread_by_bin() over bin_counts_by_group(): this
    checks DISPERSION, not direction. Built with prices deliberately
    shaped so the low-wind bucket is tightly clustered and the
    high-wind bucket is wildly scattered, and asserts the computed
    std devs actually reflect that -- not just that the cells run."""

    def _fake_get_with_spread(url, params=None, timeout=30):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if "forecast/demand/day-ahead/history" in url:
            resp.json.return_value = [{"settlementPeriod": p, "demand": 28000} for p in range(1, 7)]
        elif "forecast/generation/wind/history" in url:
            resp.json.return_value = [
                {"settlementPeriod": 1, "generation": 1000}, {"settlementPeriod": 2, "generation": 1500},
                {"settlementPeriod": 3, "generation": 2000}, {"settlementPeriod": 4, "generation": 6000},
                {"settlementPeriod": 5, "generation": 7000}, {"settlementPeriod": 6, "generation": 8000},
            ]
        elif "system-prices" in url:
            day = url.rsplit("/", 1)[-1]
            resp.json.return_value = [
                {"settlementDate": day, "settlementPeriod": 1, "systemSellPrice": 40.0,
                 "systemBuyPrice": 40.0, "netImbalanceVolume": -50.0},
                {"settlementDate": day, "settlementPeriod": 2, "systemSellPrice": 42.0,
                 "systemBuyPrice": 42.0, "netImbalanceVolume": -50.0},
                {"settlementDate": day, "settlementPeriod": 3, "systemSellPrice": 44.0,
                 "systemBuyPrice": 44.0, "netImbalanceVolume": -50.0},
                {"settlementDate": day, "settlementPeriod": 4, "systemSellPrice": 5.0,
                 "systemBuyPrice": 5.0, "netImbalanceVolume": -50.0},
                {"settlementDate": day, "settlementPeriod": 5, "systemSellPrice": 150.0,
                 "systemBuyPrice": 150.0, "netImbalanceVolume": 300.0},
                {"settlementDate": day, "settlementPeriod": 6, "systemSellPrice": 80.0,
                 "systemBuyPrice": 80.0, "netImbalanceVolume": 300.0},
            ]
        else:
            resp.json.return_value = []
        return resp

    _CALL_LOG.clear()
    CACHE_PATH.unlink(missing_ok=True)
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_with_spread):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "start_date": _FakeValue(date(2026, 6, 1)), "days_to_fetch": _FakeValue(1),
            "forecast_period_field": _FakeValue("settlementPeriod"), "demand_field": _FakeValue("demand"),
            "price_field": _FakeValue("systemSellPrice"), "niv_field": _FakeValue("netImbalanceVolume"),
            "bin_width": _FakeValue(5000),
            "wind_period_field": _FakeValue("settlementPeriod"), "wind_value_field": _FakeValue("generation"),
            "wind_bin_width": _FakeValue(5000),
        })
    CACHE_PATH.unlink(missing_ok=True)

    from wattstack_ingestion.analysis import spread_by_bin

    df = namespace["joined_with_wind_df"]
    spread = spread_by_bin(df["wind_forecast"].tolist(), df["actual_price"].tolist(), bin_width=5000.0)
    assert spread["bin_labels"] == ["0 to 5000", "5000 to 10000"]
    assert spread["std_devs"][0] < 5.0       # low-wind bucket: tightly clustered
    assert spread["std_devs"][1] > 50.0      # high-wind bucket: wildly scattered
    assert spread["std_devs"][0] < spread["std_devs"][1]


def test_gated_wind_cells_absent_when_wind_fields_not_yet_picked():
    """Demand-only analysis should still work even before the wind
    fields are chosen -- wind is additive, not a hard requirement,
    same as LOLP."""
    namespace = _run_happy_path(days=1, with_lolp_fields=False, with_wind_fields=False)
    assert "joined_df" in namespace  # demand-only join still present
    assert "joined_with_lolp_df" not in namespace
    assert "joined_with_wind_df" not in namespace  # wind join correctly gated off
