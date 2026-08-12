"""No real network calls -- requests.get is mocked throughout."""
from unittest.mock import MagicMock, patch

import pytest

from wattstack_ingestion.neso import (
    DC_REQUIREMENTS_CURRENT_URL,
    DC_REQUIREMENTS_HISTORY_URL,
    KNOWN_RESOURCES,
    SYSTEM_INERTIA_RESOURCES,
    NesoClient,
)

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
def test_datastore_search_passes_through_sort_param(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    client = NesoClient()
    client.datastore_search("some-resource-id", sort="deliveryStart desc")
    assert mock_get.call_args.kwargs["params"]["sort"] == "deliveryStart desc"


@patch("wattstack_ingestion.neso.requests.get")
def test_datastore_search_omits_sort_param_when_not_given(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    client = NesoClient()
    client.datastore_search("some-resource-id")
    assert "sort" not in mock_get.call_args.kwargs["params"]


@patch("wattstack_ingestion.neso.requests.get")
def test_datastore_search_caches_separately_per_sort_value(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response({"success": True, "result": {"records": [SAMPLE_ROW]}})
    client = NesoClient(cache=Cache(tmp_path / "c.sqlite"))
    client.datastore_search("some-resource-id", sort="deliveryStart desc")
    client.datastore_search("some-resource-id", sort="deliveryStart desc")  # cached
    client.datastore_search("some-resource-id")  # no sort -- genuinely different call
    assert mock_get.call_count == 2


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


def _mock_text_response(text_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = text_body
    resp.raise_for_status = MagicMock()
    return resp


@patch("wattstack_ingestion.neso.requests.get")
def test_fetch_csv_parses_rows_into_dicts(mock_get):
    csv_text = "settlementDate,efaBlock,dcHigh,dcLow\n2026-06-01,1,850,720\n2026-06-01,2,900,700\n"
    mock_get.return_value = _mock_text_response(csv_text)
    rows = NesoClient().fetch_csv("https://example.com/data.csv")
    assert rows == [
        {"settlementDate": "2026-06-01", "efaBlock": "1", "dcHigh": "850", "dcLow": "720"},
        {"settlementDate": "2026-06-01", "efaBlock": "2", "dcHigh": "900", "dcLow": "700"},
    ]


@patch("wattstack_ingestion.neso.requests.get")
def test_fetch_csv_caches_per_url(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_text_response("a,b\n1,2\n")
    client = NesoClient(cache=Cache(tmp_path / "c.sqlite"))
    client.fetch_csv("https://example.com/one.csv")
    client.fetch_csv("https://example.com/one.csv")  # same URL -- cached
    client.fetch_csv("https://example.com/two.csv")  # different URL -- real call
    assert mock_get.call_count == 2


@patch("wattstack_ingestion.neso.requests.get")
def test_dc_requirements_forecast_current_uses_the_confirmed_url(mock_get):
    mock_get.return_value = _mock_text_response("efaBlock,dcHigh\n1,850\n")
    NesoClient().dc_requirements_forecast_current()
    called_url = mock_get.call_args.args[0]
    assert called_url == DC_REQUIREMENTS_CURRENT_URL


@patch("wattstack_ingestion.neso.requests.get")
def test_dc_requirements_forecast_history_uses_the_confirmed_url(mock_get):
    mock_get.return_value = _mock_text_response("efaBlock,dcHigh\n1,850\n")
    NesoClient().dc_requirements_forecast_history()
    called_url = mock_get.call_args.args[0]
    assert called_url == DC_REQUIREMENTS_HISTORY_URL


@patch("wattstack_ingestion.neso.requests.get")
def test_verify_dc_requirements_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_text_response("efaBlock,dcHigh\n1,850\n")
    fields = NesoClient().verify_dc_requirements_schema()
    assert fields == {"efaBlock", "dcHigh"}


@patch("wattstack_ingestion.neso.requests.get")
def test_verify_dc_requirements_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_text_response("efaBlock,dcHigh\n")  # header only, no data rows
    with pytest.raises(RuntimeError, match="zero DC requirements records"):
        NesoClient().verify_dc_requirements_schema()


@patch("wattstack_ingestion.neso.requests.get")
def test_system_inertia_uses_the_given_resource_id(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [{"inertia": 180}]}})
    rows = NesoClient().system_inertia(SYSTEM_INERTIA_RESOURCES["2024-2025"])
    assert rows == [{"inertia": 180}]
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["resource_id"] == SYSTEM_INERTIA_RESOURCES["2024-2025"]


@patch("wattstack_ingestion.neso.requests.get")
def test_verify_system_inertia_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [{"inertia": 180}]}})
    fields = NesoClient().verify_system_inertia_schema(SYSTEM_INERTIA_RESOURCES["2024-2025"])
    assert fields == {"inertia"}


@patch("wattstack_ingestion.neso.requests.get")
def test_verify_system_inertia_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": []}})
    with pytest.raises(RuntimeError, match="zero records"):
        NesoClient().verify_system_inertia_schema("some-resource-id")
