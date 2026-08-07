"""Smoke test for notebooks/explore.py: confirms the marimo cell
graph is wired correctly (every cell's declared dependencies actually
resolve, no NameErrors, no import errors) and that cells gated behind
a "Fetch" button degrade gracefully via mo.stop() rather than
crashing when that button hasn't been clicked -- which is always true
in this headless test, since nothing here can click a button.

This does NOT test real API behaviour (no network in this
environment) or the interactive/reactive experience itself (that
needs a browser) -- it tests that the notebook, as Python, is sound.
"""
import runpy
from pathlib import Path
from unittest.mock import MagicMock, patch

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "explore.py"
CACHE_PATH = Path("wattstack_ingestion_cache.sqlite")


class _FakeValue:
    def __init__(self, v):
        self.value = v


def _load_app():
    mod = runpy.run_path(str(NOTEBOOK_PATH), run_name="not_main")
    return mod["app"]


def test_notebook_cell_graph_resolves_without_error():
    app = _load_app()
    outputs, namespace = app.run()
    assert namespace is not None


def test_fetch_buttons_default_unclicked():
    app = _load_app()
    _, namespace = app.run()
    assert namespace["fetch_general"].value is False
    assert namespace["fetch_marginal"].value is False


def test_fetch_gated_cells_do_not_populate_when_unclicked():
    """Confirms mo.stop() actually halted these cells rather than the
    dependency graph silently skipping them for some other reason --
    none of these should exist in the namespace at all."""
    app = _load_app()
    _, namespace = app.run()
    keys = set(namespace.keys())
    assert "general_df" not in keys
    assert "acceptances_df" not in keys
    assert "result" not in keys


def test_clients_and_ui_controls_are_constructed():
    """These don't depend on any button, so they should always be
    present -- if they're missing, something upstream broke."""
    app = _load_app()
    _, namespace = app.run()
    keys = set(namespace.keys())
    assert {"elexon", "neso", "cache", "source", "days_back", "marginal_days_back"} <= keys


def _fake_get_for_battery_id_test(url, params=None, timeout=30):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "bmunits" in url:
        # T_KILSB-2: a real battery with an unhelpful fuelType (the
        # confirmed real-world gap) -- only findable by ID pattern.
        # T_DUNGB-1: Dungeness B, a real non-battery station the
        # naive "ends in B-<number>" pattern will ALSO match.
        resp.json.return_value = [
            {"bmuId": "T_KILSB-2", "fuelType": "OTHER"},
            {"bmuId": "T_DUNGB-1", "fuelType": "NUCLEAR"},
        ]
    elif "acceptances" in url:
        period = (params or {}).get("settlementPeriod")
        resp.json.return_value = (
            [
                {"settlementPeriod": 1, "bmUnit": "T_KILSB-2", "levelTo": 5},
                {"settlementPeriod": 1, "bmUnit": "T_DUNGB-1", "levelTo": 5},
            ]
            if period == 1
            else []
        )
    elif "bid-offer" in url:
        period = (params or {}).get("settlementPeriod")
        resp.json.return_value = (
            [
                {"settlementPeriod": 1, "bmUnit": "T_KILSB-2", "offer": 150.0},
                {"settlementPeriod": 1, "bmUnit": "T_DUNGB-1", "offer": 90.0},
            ]
            if period == 1
            else []
        )
    else:
        resp.json.return_value = []
    return resp


def test_id_pattern_catches_a_battery_that_fuel_type_text_misses():
    """The confirmed real gap: fuelType alone (label match on
    "battery") would find nothing here -- T_KILSB-2 only shows up
    because the ID-pattern signal is unioned in."""
    CACHE_PATH.unlink(missing_ok=True)
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_for_battery_id_test):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch_general": _FakeValue(False), "days_back": _FakeValue(1), "source": _FakeValue("Elexon system prices"),
            "fetch_marginal": _FakeValue(True), "marginal_days_back": _FakeValue(1),
            "period_field": _FakeValue("settlementPeriod"), "bmu_id_field": _FakeValue("bmUnit"),
            "bo_period_field": _FakeValue("settlementPeriod"), "bo_bmu_id_field": _FakeValue("bmUnit"),
            "price_field": _FakeValue("offer"), "ref_id_field": _FakeValue("bmuId"),
            "label_field": _FakeValue("fuelType"), "battery_labels": _FakeValue("battery"),
            "id_pattern": _FakeValue(r"B-\d+$"),
        })
        assert "T_KILSB-2" in namespace["battery_ids"]
    CACHE_PATH.unlink(missing_ok=True)


def test_id_pattern_also_demonstrates_its_own_false_positive_risk():
    """Same run: the naive pattern also matches a real non-battery
    unit. This is the risk stated in analysis.py's docstring, made
    concrete -- the point isn't that this is a bug, it's that the
    match list needs checking, which the notebook's table does."""
    CACHE_PATH.unlink(missing_ok=True)
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_for_battery_id_test):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch_general": _FakeValue(False), "days_back": _FakeValue(1), "source": _FakeValue("Elexon system prices"),
            "fetch_marginal": _FakeValue(True), "marginal_days_back": _FakeValue(1),
            "period_field": _FakeValue("settlementPeriod"), "bmu_id_field": _FakeValue("bmUnit"),
            "bo_period_field": _FakeValue("settlementPeriod"), "bo_bmu_id_field": _FakeValue("bmUnit"),
            "price_field": _FakeValue("offer"), "ref_id_field": _FakeValue("bmuId"),
            "label_field": _FakeValue("fuelType"), "battery_labels": _FakeValue("battery"),
            "id_pattern": _FakeValue(r"B-\d+$"),
        })
        assert "T_DUNGB-1" in namespace["battery_ids"]  # the real, known false positive
    CACHE_PATH.unlink(missing_ok=True)
