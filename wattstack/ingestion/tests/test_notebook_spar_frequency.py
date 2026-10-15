"""Smoke test for notebooks/spar_frequency_of_system_prices.py -- same
approach as test_notebook.py: confirms the cell graph is wired
correctly and, with mocked responses, that the classification and
binning logic is actually correct end to end through the reactive
chain, not just structurally sound.
"""
import runpy
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "spar_frequency_of_system_prices.py"
CACHE_PATH = Path("wattstack_ingestion_cache.sqlite")  # matches the notebook's own relative path


@pytest.fixture(autouse=True)
def _isolated_cache():
    """The notebook uses a relative cache path -- correct for normal
    interactive use (results persist across sessions), wrong for
    tests, which need a clean cache every run or one test's fetch
    silently satisfies another's, hiding a call-count bug rather than
    testing anything."""
    CACHE_PATH.unlink(missing_ok=True)
    yield
    CACHE_PATH.unlink(missing_ok=True)


class _FakeValue:
    def __init__(self, v):
        self.value = v


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
    assert "raw_df" not in keys
    assert "clean_df" not in keys


def _fake_get(url, params=None, timeout=30):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    settlement_date = url.rsplit("/", 1)[-1]
    resp.json.return_value = [
        {"settlementDate": settlement_date, "settlementPeriod": 1, "systemSellPrice": 15.0,
         "systemBuyPrice": 15.0, "netImbalanceVolume": -300.0},
        {"settlementDate": settlement_date, "settlementPeriod": 2, "systemSellPrice": 105.0,
         "systemBuyPrice": 105.0, "netImbalanceVolume": 450.0},
        {"settlementDate": settlement_date, "settlementPeriod": 3, "systemSellPrice": 40.0,
         "systemBuyPrice": 40.0, "netImbalanceVolume": 0.0},
    ]
    return resp


def test_happy_path_classifies_long_short_correctly_end_to_end():
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "month_picker": _FakeValue(date(2026, 6, 15)),
            "price_field": _FakeValue("systemSellPrice"), "niv_field": _FakeValue("netImbalanceVolume"),
        })
        clean_df = namespace["clean_df"]
        assert len(clean_df) == 90  # 30 days x 3 periods

        # settlement_period repeats once per day, so filter + take the
        # unique value per period rather than .loc (ambiguous on a
        # non-unique index)
        def _length_for_period(period: int) -> str:
            values = clean_df.loc[clean_df["settlement_period"] == period, "system_length"].unique()
            assert len(values) == 1, f"expected one consistent length for period {period}, got {values}"
            return values[0]

        assert _length_for_period(1) == "Long"    # NIV -300
        assert _length_for_period(2) == "Short"   # NIV 450
        assert _length_for_period(3) == "Short"   # NIV 0, tie-break


def test_happy_path_fetches_one_call_per_day_in_month():
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get) as mock_get:
        app = _load_app()
        app.run(defs={
            "fetch": _FakeValue(True), "month_picker": _FakeValue(date(2026, 6, 15)),
            "price_field": _FakeValue("systemSellPrice"), "niv_field": _FakeValue("netImbalanceVolume"),
        })
        assert mock_get.call_count == 30  # June has 30 days
