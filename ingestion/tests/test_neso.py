"""No real network calls -- requests.get is mocked throughout."""
from unittest.mock import MagicMock, patch

import pytest

from wattstack_ingestion.neso import KNOWN_RESOURCES, NesoClient

SAMPLE_ROW = {"deliveryStart": "2026-07-01T00:00:00", "clearingPrice": 12.5}


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@patch("wattstack_ingestion.neso.requests.get")
def test_datastore_search_returns_records_on_success(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    client = NesoClient()
    rows = client.datastore_search("some-resource-id")
    assert rows == [SAMPLE_ROW]


@patch("wattstack_ingestion.neso.requests.get")
def test_datastore_search_raises_on_reported_failure(mock_get):
    mock_get.return_value = _mock_response({"success": False, "error": "not found"})
    client = NesoClient()
    with pytest.raises(RuntimeError, match="reported failure"):
        client.datastore_search("bad-resource-id")


@patch("wattstack_ingestion.neso.requests.get")
def test_response_reserve_results_summary_uses_known_resource_id(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    NesoClient().response_reserve_results_summary()
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["resource_id"] == KNOWN_RESOURCES["response_reserve_results_summary"]


@patch("wattstack_ingestion.neso.requests.get")
def test_response_reserve_results_by_unit_uses_known_resource_id(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    NesoClient().response_reserve_results_by_unit()
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["resource_id"] == KNOWN_RESOURCES["response_reserve_results_by_unit"]


@patch("wattstack_ingestion.neso.requests.get")
def test_dc_dr_dm_summary_historical_uses_known_resource_id(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    NesoClient().dc_dr_dm_summary_historical()
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["resource_id"] == KNOWN_RESOURCES["dc_dr_dm_summary_2021_2023"]


@patch("wattstack_ingestion.neso.requests.get")
def test_verify_schema_defaults_to_current_results_summary(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    NesoClient().verify_schema()
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["resource_id"] == KNOWN_RESOURCES["response_reserve_results_summary"]


@patch("wattstack_ingestion.neso.requests.get")
def test_verify_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    fields = NesoClient().verify_schema()
    assert fields == {"deliveryStart", "clearingPrice"}


@patch("wattstack_ingestion.neso.requests.get")
def test_verify_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": []}})
    with pytest.raises(RuntimeError, match="zero records"):
        NesoClient().verify_schema()
