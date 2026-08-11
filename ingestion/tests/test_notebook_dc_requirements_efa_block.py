"""Smoke test for notebooks/dc_requirements_by_efa_block.py -- built
against the REAL confirmed schema (Forecast_Created,
Forecast_Target_Date, Service_Type, EFA1..EFA6), not the wrong
assumption an earlier version of this notebook started with. The
important things this proves: EFA1..EFA6 unpivot into the correct
block labels, Service_Type correctly splits DC-High from DC-Low,
latest-vintage filtering picks the right row when the same target
date has multiple forecast revisions, and day-first dates parse
correctly.
"""
import runpy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "dc_requirements_by_efa_block.py"
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
    assert "requirements_df" not in keys
    assert "labelled_df" not in keys
    assert "inertia_joined_df" not in keys


_COLUMNS = [
    "Forecast_Created", "Forecast_Target_Date", "Service_Type",
    "EFA1", "EFA2", "EFA3", "EFA4", "EFA5", "EFA6",
]

_DC_CSV_ROWS = [
    # two vintages of the same target date -- latest_vintage_only should keep only the 20:00 one
    {"Forecast_Created": "01/06/2026 08:00", "Forecast_Target_Date": "02/06/2026", "Service_Type": "DCH",
     "EFA1": "400", "EFA2": "420", "EFA3": "900", "EFA4": "950", "EFA5": "1000", "EFA6": "1200"},
    {"Forecast_Created": "01/06/2026 20:00", "Forecast_Target_Date": "02/06/2026", "Service_Type": "DCH",
     "EFA1": "410", "EFA2": "430", "EFA3": "910", "EFA4": "960", "EFA5": "1010", "EFA6": "1210"},
    {"Forecast_Created": "01/06/2026 20:00", "Forecast_Target_Date": "02/06/2026", "Service_Type": "DCL",
     "EFA1": "350", "EFA2": "360", "EFA3": "850", "EFA4": "870", "EFA5": "900", "EFA6": "1100"},
]


def _fake_get(url, params=None, timeout=30):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "dcrequirements" in url:
        header = ",".join(_COLUMNS)
        lines = [",".join(str(r[c]) for c in _COLUMNS) for r in _DC_CSV_ROWS]
        resp.text = header + "\n" + "\n".join(lines) + "\n"
    elif "datastore_search" in url:
        resp.json.return_value = {"success": True, "result": {"records": [
            {"settlementDate": "02/06/2026", "inertia": 180},
        ]}}
    else:
        resp.json.return_value = {"success": True, "result": {"records": []}}
    return resp


def _run_happy_path(latest_vintage_only=True, with_inertia=False):
    defs = {
        "fetch": _FakeValue(True),
        "dc_high_value": _FakeValue("DCH"), "dc_low_value": _FakeValue("DCL"),
        "latest_vintage_only": _FakeValue(latest_vintage_only),
    }
    if with_inertia:
        defs.update({
            "inertia_year": _FakeValue("2024-2025"), "fetch_inertia": _FakeValue(True),
            "inertia_datetime_field": _FakeValue("settlementDate"), "inertia_value_field": _FakeValue("inertia"),
            "inertia_bin_width": _FakeValue(20),
        })
    with patch("wattstack_ingestion.neso.requests.get", side_effect=_fake_get):
        app = _load_app()
        _, namespace = app.run(defs=defs)
        return namespace


def test_efa_columns_unpivot_into_six_correctly_labelled_rows_per_service():
    namespace = _run_happy_path()
    df = namespace["labelled_df"]
    dc_high = df[df.service == "DC-High"]
    assert len(dc_high) == 6
    assert set(dc_high["efa_block"]) == {
        "23:00-03:00", "03:00-07:00", "07:00-11:00", "11:00-15:00", "15:00-19:00", "19:00-23:00",
    }


def test_service_type_correctly_splits_dc_high_from_dc_low():
    namespace = _run_happy_path()
    df = namespace["labelled_df"]
    assert set(df["service"].unique()) == {"DC-High", "DC-Low"}
    # values are genuinely different between the two services, not accidentally duplicated
    high_23 = df[(df.service == "DC-High") & (df.efa_block == "23:00-03:00")]["requirement_mw"].iloc[0]
    low_23 = df[(df.service == "DC-Low") & (df.efa_block == "23:00-03:00")]["requirement_mw"].iloc[0]
    assert high_23 == 410.0
    assert low_23 == 350.0


def test_latest_vintage_only_keeps_the_most_recent_forecast_created():
    namespace = _run_happy_path(latest_vintage_only=True)
    df = namespace["labelled_df"]
    dc_high_23 = df[(df.service == "DC-High") & (df.efa_block == "23:00-03:00")]
    assert len(dc_high_23) == 1
    assert dc_high_23["requirement_mw"].iloc[0] == 410.0  # the 20:00 vintage, not the 08:00 one (400)


def test_latest_vintage_toggle_off_keeps_both_vintages():
    namespace = _run_happy_path(latest_vintage_only=False)
    df = namespace["labelled_df"]
    dc_high_23 = df[(df.service == "DC-High") & (df.efa_block == "23:00-03:00")]
    assert len(dc_high_23) == 2
    assert set(dc_high_23["requirement_mw"]) == {400.0, 410.0}


def test_day_first_target_date_parsed_correctly():
    """02/06/2026 must be 2 June, not February 6th."""
    namespace = _run_happy_path()
    df = namespace["labelled_df"]
    target = df["target_date"].iloc[0]
    assert target.month == 6
    assert target.day == 2


def test_gated_inertia_cells_absent_when_not_fetched():
    namespace = _run_happy_path(with_inertia=False)
    assert "labelled_df" in namespace
    assert "inertia_joined_df" not in namespace


def test_inertia_join_filters_to_dc_high_only():
    namespace = _run_happy_path(with_inertia=True)
    df = namespace["inertia_joined_df"]
    assert len(df) == 6  # 6 EFA blocks, DC-High only -- DC-Low rows must not appear
    assert (df["inertia"] == 180.0).all()


# --- Section 3: largest secured loss ---

_BMUNITS = [
    {"bmuId": "T_SIZB-1", "fuelType": "NUCLEAR"},
    {"bmuId": "I_IFA-1", "fuelType": "INTERCONNECTOR"},
    {"bmuId": "T_GAS-1", "fuelType": "CCGT"},  # should NOT match either pattern
]

# Two genuinely distinct target dates (unlike _DC_CSV_ROWS above, which
# deliberately has two VINTAGES of the SAME date for the vintage-filter tests)
_DC_CSV_ROWS_TWO_DAYS = [
    {"Forecast_Created": "01/06/2026 20:00", "Forecast_Target_Date": "02/06/2026", "Service_Type": "DCH",
     "EFA1": "1200", "EFA2": "1210", "EFA3": "1220", "EFA4": "1230", "EFA5": "1240", "EFA6": "1250"},
    {"Forecast_Created": "01/06/2026 20:00", "Forecast_Target_Date": "02/06/2026", "Service_Type": "DCL",
     "EFA1": "1000", "EFA2": "1010", "EFA3": "1020", "EFA4": "1030", "EFA5": "1040", "EFA6": "1050"},
    {"Forecast_Created": "02/06/2026 20:00", "Forecast_Target_Date": "03/06/2026", "Service_Type": "DCH",
     "EFA1": "1400", "EFA2": "1410", "EFA3": "1420", "EFA4": "1430", "EFA5": "1440", "EFA6": "1450"},
    {"Forecast_Created": "02/06/2026 20:00", "Forecast_Target_Date": "03/06/2026", "Service_Type": "DCL",
     "EFA1": "1200", "EFA2": "1210", "EFA3": "1220", "EFA4": "1230", "EFA5": "1240", "EFA6": "1250"},
]


def _fake_get_with_losses(url, params=None, timeout=30):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    params = params or {}
    if "dcrequirements" in url:
        header = ",".join(_COLUMNS)
        lines = [",".join(str(r[c]) for c in _COLUMNS) for r in _DC_CSV_ROWS_TWO_DAYS]
        resp.text = header + "\n" + "\n".join(lines) + "\n"
    elif "bmunits" in url:
        resp.json.return_value = _BMUNITS
    elif "B1610" in url:
        # Confirmed real shape (per settlementDate + settlementPeriod, not from/to):
        # only period 1 of each day carries data in this mock, matching how the
        # actual happy-path validation for this notebook was checked live.
        _day = params.get("settlementDate")
        _period = params.get("settlementPeriod")
        if _period == 1 and _day == "2026-06-02":
            resp.json.return_value = [
                {"bmUnit": "T_SIZB-1", "quantity": 1198},
                {"bmUnit": "I_IFA-1", "quantity": -900},
                {"bmUnit": "T_GAS-1", "quantity": 5000},  # not a candidate -- must be ignored
            ]
        elif _period == 1 and _day == "2026-06-03":
            resp.json.return_value = [
                {"bmUnit": "T_SIZB-1", "quantity": 1195},
                {"bmUnit": "I_IFA-1", "quantity": -1350},  # bigger export the second day
            ]
        else:
            resp.json.return_value = []
    elif "datastore_search" in url:
        resp.json.return_value = {"success": True, "result": {"records": [
            {"settlementDate": "02/06/2026", "inertia": 150},
            {"settlementDate": "03/06/2026", "inertia": 190},
        ]}}
    else:
        resp.json.return_value = {"success": True, "result": {"records": []}}
    return resp


def _run_full_happy_path():
    defs = {
        "fetch": _FakeValue(True),
        "dc_high_value": _FakeValue("DCH"), "dc_low_value": _FakeValue("DCL"), "latest_vintage_only": _FakeValue(True),
        "inertia_year": _FakeValue("2024-2025"), "fetch_inertia": _FakeValue(True),
        "inertia_datetime_field": _FakeValue("settlementDate"), "inertia_value_field": _FakeValue("inertia"),
        "inertia_bin_width": _FakeValue(20),
        "siz_pattern": _FakeValue("SIZB"), "interconnector_pattern": _FakeValue("^I_"), "fetch_losses": _FakeValue(True),
        "ref_id_field": _FakeValue("bmuId"),
        "b1610_days_to_fetch": _FakeValue(2),
        "b1610_bmu_field": _FakeValue("bmUnit"), "b1610_value_field": _FakeValue("quantity"),
    }
    with patch("wattstack_ingestion.neso.requests.get", side_effect=_fake_get_with_losses), \
         patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_with_losses):
        app = _load_app()
        _, namespace = app.run(defs=defs)
        return namespace


def test_bmu_pattern_matching_identifies_sizb_and_interconnector_not_gas():
    namespace = _run_full_happy_path()
    ids = namespace["candidate_bmu_ids"]
    assert ids == {"T_SIZB-1", "I_IFA-1"}
    assert "T_GAS-1" not in ids


def test_largest_import_loss_is_sizb_correctly_classified_by_sign():
    namespace = _run_full_happy_path()
    import_loss = namespace["import_loss_by_day"]
    assert import_loss["2026-06-02"]["max_value"] == 1198.0
    assert import_loss["2026-06-03"]["max_value"] == 1195.0


def test_largest_export_loss_is_interconnector_correctly_classified_by_sign():
    namespace = _run_full_happy_path()
    export_loss = namespace["export_loss_by_day"]
    assert export_loss["2026-06-02"]["max_value"] == 900.0   # abs(-900)
    assert export_loss["2026-06-03"]["max_value"] == 1350.0  # abs(-1350), the bigger export


def test_unrelated_bmu_never_appears_in_loss_computation():
    """T_GAS-1 wasn't matched as a candidate -- its 5000 quantity
    must never leak into either loss figure, even though it's larger
    than SIZB's or the interconnector's output."""
    namespace = _run_full_happy_path()
    assert namespace["import_loss_by_day"]["2026-06-02"]["max_value"] == 1198.0  # not 5000


def test_dc_low_loss_comparison_table_has_correct_shape_and_values():
    namespace = _run_full_happy_path()
    df = namespace["dc_low_loss_df"]
    assert len(df) == 12  # 2 days x 6 EFA blocks
    day_1 = df[df["inertia"] == 150.0]
    assert (day_1["import_loss"] == 1198.0).all()
    day_2 = df[df["inertia"] == 190.0]
    assert (day_2["import_loss"] == 1195.0).all()


def test_dc_high_loss_comparison_table_has_correct_shape_and_values():
    namespace = _run_full_happy_path()
    df = namespace["dc_high_loss_df"]
    assert len(df) == 12
    day_1 = df[df["inertia"] == 150.0]
    assert (day_1["export_loss"] == 900.0).all()
    day_2 = df[df["inertia"] == 190.0]
    assert (day_2["export_loss"] == 1350.0).all()


def test_section_3_cells_absent_when_losses_not_fetched():
    defs = {
        "fetch": _FakeValue(True),
        "dc_high_value": _FakeValue("DCH"), "dc_low_value": _FakeValue("DCL"), "latest_vintage_only": _FakeValue(True),
    }
    with patch("wattstack_ingestion.neso.requests.get", side_effect=_fake_get_with_losses), \
         patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_with_losses):
        app = _load_app()
        _, namespace = app.run(defs=defs)
    assert "labelled_df" in namespace  # Sections 1 still work independently
    assert "candidate_bmu_ids" not in namespace
    assert "dc_low_loss_df" not in namespace
