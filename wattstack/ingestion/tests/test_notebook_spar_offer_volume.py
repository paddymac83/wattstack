"""Smoke test for notebooks/spar_accepted_offer_volume_by_fuel_type.py
-- same approach as the other notebook tests: confirms the cell graph
resolves and, with mocked responses, that offer-direction inference,
fuel-type categorisation, and BSAA handling are correct end to end
through the reactive chain.
"""
import runpy
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "spar_accepted_offer_volume_by_fuel_type.py"
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
    if "bmunits" in url:
        resp.json.return_value = [
            {"bmuId": "T_GAS-1", "fuelType": "CCGT"},
            {"bmuId": "T_WIND-1", "fuelType": "WIND"},
        ]
    elif "disbsad" in url:
        period = (params or {}).get("settlementPeriod")
        resp.json.return_value = [{"bmUnit": "BSAA-STOR", "volume": 20.0}] if period == 1 else []
    elif "acceptances" in url:
        period = (params or {}).get("settlementPeriod")
        if period == 1:
            resp.json.return_value = [
                {"bmUnit": "T_GAS-1", "settlementPeriod": 1, "levelFrom": 100.0, "levelTo": 150.0},
                {"bmUnit": "T_WIND-1", "settlementPeriod": 1, "levelFrom": 80.0, "levelTo": 60.0},
            ]
        else:
            resp.json.return_value = []
    else:
        resp.json.return_value = []
    return resp


def _fake_get_bess(url, params=None, timeout=30):
    """Same shape, but the reference data includes a real battery
    whose fuelType is unhelpful -- the confirmed real-world gap."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "bmunits" in url:
        resp.json.return_value = [
            {"bmuId": "T_KILSB-2", "fuelType": "OTHER"},
            {"bmuId": "T_GAS-1", "fuelType": "CCGT"},
        ]
    elif "disbsad" in url:
        resp.json.return_value = []
    elif "acceptances" in url:
        period = (params or {}).get("settlementPeriod")
        if period == 1:
            resp.json.return_value = [
                {"bmUnit": "T_KILSB-2", "settlementPeriod": 1, "levelFrom": 100.0, "levelTo": 150.0},
                {"bmUnit": "T_GAS-1", "settlementPeriod": 1, "levelFrom": 100.0, "levelTo": 130.0},
            ]
        else:
            resp.json.return_value = []
    else:
        resp.json.return_value = []
    return resp


def _run_happy_path(days=2):
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get) as mock_get:
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "month_picker": _FakeValue(date(2026, 6, 15)), "days_to_fetch": _FakeValue(days),
            "bmu_id_field": _FakeValue("bmUnit"), "level_from_field": _FakeValue("levelFrom"),
            "level_to_field": _FakeValue("levelTo"), "bsad_volume_field": _FakeValue("volume"),
            "ref_id_field": _FakeValue("bmuId"), "fuel_field": _FakeValue("fuelType"),
            "battery_labels": _FakeValue("battery"), "id_pattern": _FakeValue(""),
        })
        return namespace, mock_get.call_count


def test_offer_direction_action_is_categorised_and_summed_correctly():
    namespace, _ = _run_happy_path(days=2)
    df = namespace["categorised_df"]
    assert df[df.category == "CCGT"]["volume"].sum() == 100.0  # 50/day x 2 days


def test_bsaa_volume_is_categorised_and_summed_correctly():
    namespace, _ = _run_happy_path(days=2)
    df = namespace["categorised_df"]
    assert df[df.category == "BSAA"]["volume"].sum() == 40.0  # 20/day x 2 days


def test_bid_direction_action_is_excluded_not_zero_filled():
    """The Wind action decreases (levelTo < levelFrom) -- offer_volume
    is 0 for it, and the notebook filters volume>0, so it should be
    entirely absent, not present with a zero."""
    namespace, _ = _run_happy_path(days=2)
    df = namespace["categorised_df"]
    assert "WIND" not in df["category"].unique()


def test_days_to_fetch_is_respected_not_the_full_month():
    _, call_count = _run_happy_path(days=2)
    # 2 days x (48 acceptances + 48 disbsad) = 192, plus 1 for bm_units_reference
    assert call_count == 193


def test_battery_matched_by_id_pattern_gets_forced_into_bess_category():
    """The confirmed real gap: T_KILSB-2's raw fuelType is 'OTHER',
    not battery-like at all. Without the fix this would show up
    stacked under 'OTHER' or 'Unknown'; with it, it's forced to its
    own 'BESS' segment."""
    CACHE_PATH.unlink(missing_ok=True)
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_bess):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "month_picker": _FakeValue(date(2026, 6, 15)), "days_to_fetch": _FakeValue(1),
            "bmu_id_field": _FakeValue("bmUnit"), "level_from_field": _FakeValue("levelFrom"),
            "level_to_field": _FakeValue("levelTo"), "bsad_volume_field": _FakeValue("volume"),
            "ref_id_field": _FakeValue("bmuId"), "fuel_field": _FakeValue("fuelType"),
            "battery_labels": _FakeValue("battery"), "id_pattern": _FakeValue(r"B-\d+$"),
        })
        df = namespace["categorised_df"]
        assert df[df.bmu_or_source == "T_KILSB-2"]["category"].iloc[0] == "BESS"
    CACHE_PATH.unlink(missing_ok=True)


def test_non_battery_unit_keeps_its_real_fuel_type():
    """T_GAS-1 shouldn't be swept into BESS just because a battery
    was matched elsewhere in the same run."""
    CACHE_PATH.unlink(missing_ok=True)
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_bess):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "month_picker": _FakeValue(date(2026, 6, 15)), "days_to_fetch": _FakeValue(1),
            "bmu_id_field": _FakeValue("bmUnit"), "level_from_field": _FakeValue("levelFrom"),
            "level_to_field": _FakeValue("levelTo"), "bsad_volume_field": _FakeValue("volume"),
            "ref_id_field": _FakeValue("bmuId"), "fuel_field": _FakeValue("fuelType"),
            "battery_labels": _FakeValue("battery"), "id_pattern": _FakeValue(r"B-\d+$"),
        })
        df = namespace["categorised_df"]
        assert df[df.bmu_or_source == "T_GAS-1"]["category"].iloc[0] == "CCGT"
    CACHE_PATH.unlink(missing_ok=True)


def test_battery_ids_empty_when_no_pattern_and_no_fuel_type_label_match():
    """Without an ID pattern, and with a fuelType that doesn't say
    anything battery-like, nothing should match -- confirms the
    override doesn't fire spuriously."""
    CACHE_PATH.unlink(missing_ok=True)
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_bess):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "month_picker": _FakeValue(date(2026, 6, 15)), "days_to_fetch": _FakeValue(1),
            "bmu_id_field": _FakeValue("bmUnit"), "level_from_field": _FakeValue("levelFrom"),
            "level_to_field": _FakeValue("levelTo"), "bsad_volume_field": _FakeValue("volume"),
            "ref_id_field": _FakeValue("bmuId"), "fuel_field": _FakeValue("fuelType"),
            "battery_labels": _FakeValue("battery"), "id_pattern": _FakeValue(""),
        })
        assert namespace["battery_ids"] == set()
        df = namespace["categorised_df"]
        assert "BESS" not in df["category"].unique()
    CACHE_PATH.unlink(missing_ok=True)
