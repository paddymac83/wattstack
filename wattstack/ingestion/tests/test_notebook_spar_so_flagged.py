"""Smoke test for notebooks/spar_so_flagged_daily_volume.py -- same
approach as the other notebook tests: confirms the cell graph
resolves and, with mocked responses covering several plausible real
flag-value encodings, that direction inference and flag normalisation
are correct end to end through the reactive chain.
"""
import runpy
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "spar_so_flagged_daily_volume.py"
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
    assert "acceptances_df" not in keys
    assert "categorised_df" not in keys


def _fake_get(url, params=None, timeout=30):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    period = (params or {}).get("settlementPeriod")
    if period == 1:
        resp.json.return_value = [
            {"levelFrom": 100.0, "levelTo": 150.0, "soFlag": True},   # Buy, SO-Flagged
            {"levelFrom": 100.0, "levelTo": 140.0, "soFlag": "N"},    # Buy, Unflagged
            {"levelFrom": 100.0, "levelTo": 60.0, "soFlag": "Y"},     # Sell, SO-Flagged
            {"levelFrom": 100.0, "levelTo": 100.0, "soFlag": False},  # no direction
        ]
    else:
        resp.json.return_value = []
    return resp


def _run_happy_path():
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "month_picker": _FakeValue(date(2026, 6, 15)), "days_to_fetch": _FakeValue(1),
            "level_from_field": _FakeValue("levelFrom"), "level_to_field": _FakeValue("levelTo"),
            "so_flag_field": _FakeValue("soFlag"),
        })
        return namespace


def test_no_net_direction_row_is_dropped():
    namespace = _run_happy_path()
    df = namespace["categorised_df"]
    assert len(df) == 3  # the flat 100->100 row contributes nothing either direction


def test_buy_so_flagged_volume_is_correct():
    namespace = _run_happy_path()
    df = namespace["categorised_df"]
    assert df[(df.direction == "Buy") & (df.flag == "SO-Flagged")]["volume"].sum() == 50.0


def test_buy_unflagged_volume_is_correct():
    namespace = _run_happy_path()
    df = namespace["categorised_df"]
    assert df[(df.direction == "Buy") & (df.flag == "Unflagged")]["volume"].sum() == 40.0


def test_sell_so_flagged_volume_is_correct():
    namespace = _run_happy_path()
    df = namespace["categorised_df"]
    assert df[(df.direction == "Sell") & (df.flag == "SO-Flagged")]["volume"].sum() == 40.0


def test_mixed_flag_encodings_all_normalise_correctly():
    """True, 'N', and 'Y' all appear in the mocked data -- if
    is_flagged() mishandled any of them, the per-direction sums above
    would already be wrong, but this asserts it directly against the
    raw category labels."""
    namespace = _run_happy_path()
    df = namespace["categorised_df"]
    assert set(df["flag"].unique()) == {"SO-Flagged", "Unflagged"}
