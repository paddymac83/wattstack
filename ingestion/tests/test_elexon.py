"""No real network calls -- requests.get is mocked throughout, matching
glasshouse's ingestion test discipline."""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from wattstack_ingestion.elexon import ElexonClient


SAMPLE_ROW = {
    "settlementDate": "2026-07-01",
    "settlementPeriod": 1,
    "systemSellPrice": 55.5,
    "systemBuyPrice": 55.5,
}


def _mock_response(json_body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@patch("wattstack_ingestion.elexon.requests.get")
def test_system_prices_returns_bare_list_response(mock_get):
    mock_get.return_value = _mock_response([SAMPLE_ROW])
    client = ElexonClient()
    rows = client.system_prices(date(2026, 7, 1))
    assert rows == [SAMPLE_ROW]


@patch("wattstack_ingestion.elexon.requests.get")
def test_system_prices_handles_data_envelope_response(mock_get):
    mock_get.return_value = _mock_response({"data": [SAMPLE_ROW]})
    client = ElexonClient()
    rows = client.system_prices(date(2026, 7, 1))
    assert rows == [SAMPLE_ROW]


@patch("wattstack_ingestion.elexon.requests.get")
def test_system_prices_uses_cache_on_second_call(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([SAMPLE_ROW])
    client = ElexonClient(cache=Cache(tmp_path / "c.sqlite"))
    client.system_prices(date(2026, 7, 1))
    client.system_prices(date(2026, 7, 1))
    assert mock_get.call_count == 1


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_schema_passes_when_expected_fields_present(mock_get):
    mock_get.return_value = _mock_response([SAMPLE_ROW])
    client = ElexonClient()
    fields = client.verify_schema(date(2026, 7, 1))
    assert {"settlementDate", "settlementPeriod", "systemSellPrice", "systemBuyPrice"} <= fields


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_schema_raises_clearly_when_fields_missing(mock_get):
    mock_get.return_value = _mock_response([{"unexpectedField": 1}])
    client = ElexonClient()
    with pytest.raises(RuntimeError, match="missing expected fields"):
        client.verify_schema(date(2026, 7, 1))


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response([])
    client = ElexonClient()
    with pytest.raises(RuntimeError, match="zero system-price records"):
        client.verify_schema(date(2026, 7, 1))


@patch("wattstack_ingestion.elexon.requests.get")
def test_bid_offer_acceptances_uses_query_params_not_path_segments(mock_get):
    row = {"bmUnit": "T_BATT-1", "settlementPeriod": 5, "levelFrom": 0, "levelTo": 50}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.bid_offer_acceptances(date(2026, 7, 1), 5)
    assert rows == [row]
    called_url = mock_get.call_args.args[0]
    assert called_url == (
        "https://data.elexon.co.uk/bmrs/api/v1/balancing/acceptances/all"
        "?settlementDate=2026-07-01&settlementPeriod=5"
    )


@patch("wattstack_ingestion.elexon.requests.get")
def test_bid_offer_data_uses_query_params(mock_get):
    row = {"bmUnit": "T_BATT-1", "settlementPeriod": 5, "offer": 120.0, "bid": 80.0}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.bid_offer_data(date(2026, 7, 1), 5)
    assert rows == [row]
    called_url = mock_get.call_args.args[0]
    assert called_url == (
        "https://data.elexon.co.uk/bmrs/api/v1/balancing/bid-offer/all"
        "?settlementDate=2026-07-01&settlementPeriod=5"
    )


@patch("wattstack_ingestion.elexon.requests.get")
def test_bid_offer_data_is_cached_separately_from_acceptances(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([{"bmUnit": "T_BATT-1"}])
    cache = Cache(tmp_path / "c.sqlite")
    client = ElexonClient(cache=cache)
    client.bid_offer_acceptances(date(2026, 7, 1), 1)
    client.bid_offer_data(date(2026, 7, 1), 1)
    assert mock_get.call_count == 2  # different cache keys, both real calls


@patch("wattstack_ingestion.elexon.requests.get")
def test_bid_offer_data_for_day_makes_48_requests(mock_get):
    mock_get.return_value = _mock_response([{"bmUnit": "T_BATT-1"}])
    client = ElexonClient()
    rows = client.bid_offer_data_for_day(date(2026, 7, 1))
    assert mock_get.call_count == 48
    assert len(rows) == 48


@patch("wattstack_ingestion.elexon.requests.get")
def test_bid_offer_acceptances_for_day_makes_48_requests(mock_get):
    mock_get.return_value = _mock_response([{"bmUnit": "T_BATT-1"}])
    client = ElexonClient()
    rows = client.bid_offer_acceptances_for_day(date(2026, 7, 1))
    assert mock_get.call_count == 48
    assert len(rows) == 48  # one record per period in this mock


@patch("wattstack_ingestion.elexon.requests.get")
def test_bid_offer_acceptances_for_day_caches_each_period_independently(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([{"bmUnit": "T_BATT-1"}])
    cache = Cache(tmp_path / "c.sqlite")
    client = ElexonClient(cache=cache)
    client.bid_offer_acceptances_for_day(date(2026, 7, 1))
    mock_get.reset_mock()
    # re-running the same day should hit the cache for all 48 periods
    client.bid_offer_acceptances_for_day(date(2026, 7, 1))
    assert mock_get.call_count == 0


@patch("wattstack_ingestion.elexon.requests.get")
def test_bm_units_reference_returns_records_and_is_cached(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    row = {"bmUnitId": "T_BATT-1", "fuelType": "BATTERY"}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient(cache=Cache(tmp_path / "c.sqlite"))
    client.bm_units_reference()
    client.bm_units_reference()
    assert mock_get.call_count == 1
