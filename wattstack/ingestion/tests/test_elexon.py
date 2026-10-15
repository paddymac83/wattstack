"""No real network calls -- requests.get is mocked throughout, matching
glasshouse's ingestion test discipline."""
from datetime import date, datetime, timedelta
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
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/balancing/acceptances/all"
    assert mock_get.call_args.kwargs["params"] == {"settlementDate": "2026-07-01", "settlementPeriod": 5}


@patch("wattstack_ingestion.elexon.requests.get")
def test_bid_offer_data_uses_query_params(mock_get):
    row = {"bmUnit": "T_BATT-1", "settlementPeriod": 5, "offer": 120.0, "bid": 80.0}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.bid_offer_data(date(2026, 7, 1), 5)
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/balancing/bid-offer/all"
    assert mock_get.call_args.kwargs["params"] == {"settlementDate": "2026-07-01", "settlementPeriod": 5}


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
def test_disaggregated_bsad_uses_query_params(mock_get):
    row = {"bmUnit": "T_BSAA-1", "settlementPeriod": 5, "volume": 12.5}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.disaggregated_bsad(date(2026, 7, 1), 5)
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/balancing/nonbm/disbsad/details"
    assert mock_get.call_args.kwargs["params"] == {"settlementDate": "2026-07-01", "settlementPeriod": 5}


@patch("wattstack_ingestion.elexon.requests.get")
def test_disaggregated_bsad_for_day_makes_48_requests(mock_get):
    mock_get.return_value = _mock_response([{"bmUnit": "T_BSAA-1"}])
    client = ElexonClient()
    rows = client.disaggregated_bsad_for_day(date(2026, 7, 1))
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


@patch("wattstack_ingestion.elexon.requests.get")
def test_demand_forecast_day_ahead_uses_confirmed_url(mock_get):
    row = {"settlementDate": "2026-08-01", "settlementPeriod": 1, "demand": 25000}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.demand_forecast_day_ahead()
    assert rows == [row]
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://data.elexon.co.uk/bmrs/api/v1/forecast/demand/day-ahead"


@patch("wattstack_ingestion.elexon.requests.get")
def test_demand_forecast_day_ahead_history_uses_publish_time_query_param(mock_get):
    row = {"settlementDate": "2026-08-01", "settlementPeriod": 1, "demand": 25000}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    publish_time = datetime(2026, 7, 31, 9, 0, 0)
    rows = client.demand_forecast_day_ahead_history(publish_time)
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/forecast/demand/day-ahead/history"
    assert mock_get.call_args.kwargs["params"] == {"publishTime": "2026-07-31T09:00:00"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_demand_forecast_history_caches_per_publish_time(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([{"demand": 25000}])
    client = ElexonClient(cache=Cache(tmp_path / "c.sqlite"))
    t1 = datetime(2026, 7, 31, 9, 0, 0)
    t2 = datetime(2026, 7, 30, 9, 0, 0)
    client.demand_forecast_day_ahead_history(t1)
    client.demand_forecast_day_ahead_history(t1)  # same vintage -- cached
    client.demand_forecast_day_ahead_history(t2)  # different vintage -- real call
    assert mock_get.call_count == 2


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_demand_forecast_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_response([{"settlementDate": "2026-08-01", "demand": 25000}])
    fields = ElexonClient().verify_demand_forecast_schema()
    assert fields == {"settlementDate", "demand"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_demand_forecast_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response([])
    with pytest.raises(RuntimeError, match="zero demand forecast records"):
        ElexonClient().verify_demand_forecast_schema()


@patch("wattstack_ingestion.elexon.requests.get")
def test_loss_of_load_forecast_uses_confirmed_from_to_params(mock_get):
    row = {"settlementDate": "2026-08-01", "settlementPeriod": 1, "lolp1h": 0.01}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.loss_of_load_forecast(datetime(2026, 8, 1), datetime(2026, 8, 2))
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/forecast/system/loss-of-load"
    assert mock_get.call_args.kwargs["params"] == {"from": "2026-08-01T00:00:00", "to": "2026-08-02T00:00:00"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_loss_of_load_forecast_includes_optional_settlement_period_params(mock_get):
    mock_get.return_value = _mock_response([{"demand": 1}])
    client = ElexonClient()
    client.loss_of_load_forecast(
        datetime(2026, 8, 1), datetime(2026, 8, 2), settlement_period_from=10, settlement_period_to=20
    )
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["settlementPeriodFrom"] == 10
    assert called_params["settlementPeriodTo"] == 20


@patch("wattstack_ingestion.elexon.requests.get")
def test_loss_of_load_forecast_omits_settlement_period_params_when_not_given(mock_get):
    mock_get.return_value = _mock_response([{"demand": 1}])
    client = ElexonClient()
    client.loss_of_load_forecast(datetime(2026, 8, 1), datetime(2026, 8, 2))
    called_params = mock_get.call_args.kwargs["params"]
    assert "settlementPeriodFrom" not in called_params
    assert "settlementPeriodTo" not in called_params


@patch("wattstack_ingestion.elexon.requests.get")
def test_timezone_aware_publish_time_plus_offset_goes_through_params_not_the_url_string(mock_get):
    """The actual bug, reproduced directly: a tz-aware datetime's
    isoformat() contains a literal '+' (e.g. '+00:00'), which means
    'space' if it ends up unescaped in a URL string. This asserts the
    raw '+'-containing string is passed via params -- where requests
    percent-encodes it correctly -- not concatenated into the url
    argument, which is exactly how this broke live."""
    from datetime import timezone

    mock_get.return_value = _mock_response([{"demand": 1}])
    client = ElexonClient()
    publish_time = datetime(2026, 7, 31, 10, 0, 0, tzinfo=timezone.utc)
    client.demand_forecast_day_ahead_history(publish_time)

    called_url = mock_get.call_args.args[0]
    called_params = mock_get.call_args.kwargs["params"]
    assert "+" not in called_url  # nothing timestamp-shaped leaked into the URL itself
    assert called_params["publishTime"] == "2026-07-31T10:00:00+00:00"


@patch("wattstack_ingestion.elexon.requests.get")
def test_loss_of_load_forecast_caches_per_unique_range(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([{"demand": 1}])
    client = ElexonClient(cache=Cache(tmp_path / "c.sqlite"))
    client.loss_of_load_forecast(datetime(2026, 8, 1), datetime(2026, 8, 2))
    client.loss_of_load_forecast(datetime(2026, 8, 1), datetime(2026, 8, 2))  # same range -- cached
    client.loss_of_load_forecast(datetime(2026, 8, 2), datetime(2026, 8, 3))  # different range -- real call
    assert mock_get.call_count == 2


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_loss_of_load_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "lolp1h": 0.01}])
    fields = ElexonClient().verify_loss_of_load_schema(datetime(2026, 8, 1), datetime(2026, 8, 2))
    assert fields == {"settlementPeriod", "lolp1h"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_loss_of_load_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response([])
    with pytest.raises(RuntimeError, match="zero loss-of-load records"):
        ElexonClient().verify_loss_of_load_schema(datetime(2026, 8, 1), datetime(2026, 8, 2))


@patch("wattstack_ingestion.elexon.requests.get")
def test_wind_forecast_uses_confirmed_from_to_params(mock_get):
    row = {"settlementDate": "2026-08-01", "settlementPeriod": 1, "generation": 8500}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.wind_forecast(datetime(2026, 8, 1), datetime(2026, 8, 2))
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/forecast/generation/wind"
    assert mock_get.call_args.kwargs["params"] == {"from": "2026-08-01T00:00:00", "to": "2026-08-02T00:00:00"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_wind_forecast_history_uses_publish_time_param(mock_get):
    row = {"settlementDate": "2026-08-01", "settlementPeriod": 1, "generation": 8500}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    publish_time = datetime(2026, 7, 31, 8, 30, 0)
    rows = client.wind_forecast_history(publish_time)
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/forecast/generation/wind/history"
    assert mock_get.call_args.kwargs["params"] == {"publishTime": "2026-07-31T08:30:00"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_wind_forecast_history_caches_per_publish_time(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([{"generation": 8500}])
    client = ElexonClient(cache=Cache(tmp_path / "c.sqlite"))
    t1 = datetime(2026, 7, 31, 8, 30, 0)
    t2 = datetime(2026, 7, 30, 8, 30, 0)
    client.wind_forecast_history(t1)
    client.wind_forecast_history(t1)  # same vintage -- cached
    client.wind_forecast_history(t2)  # different vintage -- real call
    assert mock_get.call_count == 2


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_wind_forecast_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "generation": 8500}])
    fields = ElexonClient().verify_wind_forecast_schema(datetime(2026, 8, 1), datetime(2026, 8, 2))
    assert fields == {"settlementPeriod", "generation"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_wind_forecast_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response([])
    with pytest.raises(RuntimeError, match="zero wind forecast records"):
        ElexonClient().verify_wind_forecast_schema(datetime(2026, 8, 1), datetime(2026, 8, 2))


@patch("wattstack_ingestion.elexon.requests.get")
def test_actual_generation_per_bmu_uses_confirmed_settlement_date_and_period_params(mock_get):
    row = {"settlementDate": "2026-06-01", "bmUnit": "T_SIZB-1", "quantity": 1200}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.actual_generation_per_bmu(date(2026, 6, 1), 10)
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610"
    assert mock_get.call_args.kwargs["params"] == {"settlementDate": "2026-06-01", "settlementPeriod": 10}


@patch("wattstack_ingestion.elexon.requests.get")
def test_actual_generation_per_bmu_matches_the_real_confirmed_url_exactly(mock_get):
    """Regression test against the exact URL confirmed live: an
    earlier version of this method used from/to, sourced from a
    third-party wrapper's convenience parameter names rather than
    Elexon's own API documentation -- this pins the real shape
    directly so that mistake can't quietly come back."""
    mock_get.return_value = _mock_response([{"quantity": 1}])
    client = ElexonClient()
    client.actual_generation_per_bmu(date(2022, 8, 12), 10)
    called_url = mock_get.call_args.args[0]
    called_params = mock_get.call_args.kwargs["params"]
    assert called_url == "https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610"
    assert called_params == {"settlementDate": "2022-08-12", "settlementPeriod": 10}


@patch("wattstack_ingestion.elexon.requests.get")
def test_actual_generation_per_bmu_includes_optional_bmunit_filter(mock_get):
    mock_get.return_value = _mock_response([{"quantity": 1}])
    client = ElexonClient()
    client.actual_generation_per_bmu(date(2026, 6, 1), 10, bm_unit_id="T_SIZB-1")
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["bmUnit"] == "T_SIZB-1"


@patch("wattstack_ingestion.elexon.requests.get")
def test_actual_generation_per_bmu_omits_bmunit_filter_when_not_given(mock_get):
    mock_get.return_value = _mock_response([{"quantity": 1}])
    client = ElexonClient()
    client.actual_generation_per_bmu(date(2026, 6, 1), 10)
    called_params = mock_get.call_args.kwargs["params"]
    assert "bmUnit" not in called_params


@patch("wattstack_ingestion.elexon.requests.get")
def test_actual_generation_per_bmu_caches_per_unique_call(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([{"quantity": 1}])
    client = ElexonClient(cache=Cache(tmp_path / "c.sqlite"))
    client.actual_generation_per_bmu(date(2026, 6, 1), 10, bm_unit_id="T_SIZB-1")
    client.actual_generation_per_bmu(date(2026, 6, 1), 10, bm_unit_id="T_SIZB-1")  # cached
    client.actual_generation_per_bmu(date(2026, 6, 1), 11, bm_unit_id="T_SIZB-1")  # different period -- real call
    assert mock_get.call_count == 2


@patch("wattstack_ingestion.elexon.requests.get")
def test_actual_generation_per_bmu_for_day_makes_48_requests(mock_get):
    mock_get.return_value = _mock_response([{"bmUnit": "T_SIZB-1", "quantity": 1200}])
    client = ElexonClient()
    rows = client.actual_generation_per_bmu_for_day(date(2026, 6, 1))
    assert mock_get.call_count == 48
    assert len(rows) == 48


@patch("wattstack_ingestion.elexon.requests.get")
def test_actual_generation_per_bmu_for_day_passes_through_bmunit_filter(mock_get):
    mock_get.return_value = _mock_response([{"quantity": 1}])
    client = ElexonClient()
    client.actual_generation_per_bmu_for_day(date(2026, 6, 1), bm_unit_id="T_SIZB-1")
    for call in mock_get.call_args_list:
        assert call.kwargs["params"]["bmUnit"] == "T_SIZB-1"


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_actual_generation_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_response([{"bmUnit": "T_SIZB-1", "quantity": 1200}])
    fields = ElexonClient().verify_actual_generation_schema(date(2026, 6, 1), 10)
    assert fields == {"bmUnit", "quantity"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_actual_generation_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response([])
    with pytest.raises(RuntimeError, match="zero B1610 records"):
        ElexonClient().verify_actual_generation_schema(date(2026, 6, 1), 10)


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_uses_the_confirmed_datasets_mid_endpoint(mock_get):
    row = {"settlementDate": "2026-06-01", "settlementPeriod": 10, "dataProvider": "N2EX", "price": 65.4}
    mock_get.return_value = _mock_response([row])
    client = ElexonClient()
    rows = client.market_index_data(datetime(2026, 6, 1), datetime(2026, 6, 2))
    assert rows == [row]
    assert mock_get.call_args.args[0] == "https://data.elexon.co.uk/bmrs/api/v1/datasets/MID"
    assert mock_get.call_args.kwargs["params"] == {
        "from": "2026-06-01T00:00:00", "to": "2026-06-02T00:00:00", "dataProviders": ["APXMIDP"],
    }


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_matches_the_real_confirmed_url_shape(mock_get):
    """Regression test against the confirmed live URL: an earlier
    version of this method used the wrong endpoint entirely
    (/balancing/pricing/market-index with settlementDate/
    settlementPeriod) -- this pins the real endpoint and parameter
    names directly so that mistake can't quietly come back.

    N2EXMIDP passed explicitly here, matching the real confirmed URL
    exactly -- independent of whatever the default provider is (later
    changed to APXMIDP once live data showed N2EXMIDP returning
    zeroes; that's a separate finding from the endpoint/parameter
    shape this test exists to pin)."""
    mock_get.return_value = _mock_response([{"price": 1}])
    client = ElexonClient()
    client.market_index_data(datetime(2022, 8, 12), datetime(2022, 8, 13), data_providers=["N2EXMIDP"])
    called_url = mock_get.call_args.args[0]
    called_params = mock_get.call_args.kwargs["params"]
    assert called_url == "https://data.elexon.co.uk/bmrs/api/v1/datasets/MID"
    assert called_params["from"] == "2022-08-12T00:00:00"
    assert called_params["to"] == "2022-08-13T00:00:00"
    assert called_params["dataProviders"] == ["N2EXMIDP"]


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_accepts_multiple_data_providers(mock_get):
    mock_get.return_value = _mock_response([{"price": 1}])
    client = ElexonClient()
    client.market_index_data(datetime(2026, 6, 1), datetime(2026, 6, 2), data_providers=["N2EXMIDP", "APXMIDP"])
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["dataProviders"] == ["N2EXMIDP", "APXMIDP"]


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_covers_a_full_day_in_one_call(mock_get):
    """The real efficiency win from the correction: MID is a genuine
    date-range endpoint, not per-period -- one call for a whole day,
    not 48."""
    mock_get.return_value = _mock_response([{"price": 1}] * 48)
    client = ElexonClient()
    rows = client.market_index_data(datetime(2026, 6, 1), datetime(2026, 6, 2))
    assert mock_get.call_count == 1
    assert len(rows) == 48


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_caches_per_unique_range_and_providers(mock_get, tmp_path):
    from wattstack_ingestion.cache import Cache

    mock_get.return_value = _mock_response([{"price": 60.0}])
    client = ElexonClient(cache=Cache(tmp_path / "c.sqlite"))
    client.market_index_data(datetime(2026, 6, 1), datetime(2026, 6, 2))
    client.market_index_data(datetime(2026, 6, 1), datetime(2026, 6, 2))  # cached
    client.market_index_data(datetime(2026, 6, 2), datetime(2026, 6, 3))  # different range -- real call
    assert mock_get.call_count == 2


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_mid_schema_returns_field_names(mock_get):
    mock_get.return_value = _mock_response([{"price": 60.0, "dataProvider": "N2EX"}])
    fields = ElexonClient().verify_mid_schema(datetime(2026, 6, 1), datetime(2026, 6, 2))
    assert fields == {"price", "dataProvider"}


@patch("wattstack_ingestion.elexon.requests.get")
def test_verify_mid_schema_raises_clearly_when_no_records(mock_get):
    mock_get.return_value = _mock_response([])
    with pytest.raises(RuntimeError, match="zero MID records"):
        ElexonClient().verify_mid_schema(datetime(2026, 6, 1), datetime(2026, 6, 2))


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_range_chunks_into_7_day_windows(mock_get):
    """Confirmed live: MID only allows a 7-day span per call. A
    28-day request must become 4 separate <=7-day calls, not one."""
    mock_get.return_value = _mock_response([{"price": 50.0}])
    client = ElexonClient()
    client.market_index_data_range(datetime(2026, 6, 1), datetime(2026, 6, 29))
    assert mock_get.call_count == 4
    for call in mock_get.call_args_list:
        params = call.kwargs["params"]
        span = datetime.fromisoformat(params["to"]) - datetime.fromisoformat(params["from"])
        assert span <= timedelta(days=7)


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_range_handles_a_non_exact_multiple_of_7_days(mock_get):
    """10 days must become two chunks (7 + 3), not silently drop the remainder."""
    mock_get.return_value = _mock_response([{"price": 50.0}])
    client = ElexonClient()
    client.market_index_data_range(datetime(2026, 6, 1), datetime(2026, 6, 11))
    assert mock_get.call_count == 2
    called_tos = sorted(call.kwargs["params"]["to"] for call in mock_get.call_args_list)
    assert called_tos[-1] == "2026-06-11T00:00:00"  # the final chunk ends exactly at the requested range end


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_range_covers_the_full_range_with_no_gaps_or_overlaps(mock_get):
    mock_get.return_value = _mock_response([{"price": 50.0}])
    client = ElexonClient()
    client.market_index_data_range(datetime(2026, 6, 1), datetime(2026, 6, 29))
    chunks = sorted(
        (datetime.fromisoformat(c.kwargs["params"]["from"]), datetime.fromisoformat(c.kwargs["params"]["to"]))
        for c in mock_get.call_args_list
    )
    assert chunks[0][0] == datetime(2026, 6, 1)
    assert chunks[-1][1] == datetime(2026, 6, 29)
    for i in range(len(chunks) - 1):
        assert chunks[i][1] == chunks[i + 1][0]  # each chunk's end is exactly the next chunk's start


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_range_within_7_days_makes_a_single_call(mock_get):
    mock_get.return_value = _mock_response([{"price": 50.0}])
    client = ElexonClient()
    client.market_index_data_range(datetime(2026, 6, 1), datetime(2026, 6, 4))
    assert mock_get.call_count == 1


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_range_aggregates_records_from_every_chunk(mock_get):
    mock_get.return_value = _mock_response([{"price": 50.0}, {"price": 55.0}])
    client = ElexonClient()
    records = client.market_index_data_range(datetime(2026, 6, 1), datetime(2026, 6, 15))  # 2 chunks
    assert len(records) == 4  # 2 records x 2 chunks


@patch("wattstack_ingestion.elexon.requests.get")
def test_market_index_data_range_passes_through_data_providers(mock_get):
    mock_get.return_value = _mock_response([{"price": 50.0}])
    client = ElexonClient()
    client.market_index_data_range(datetime(2026, 6, 1), datetime(2026, 6, 4), data_providers=["APXMIDP"])
    assert mock_get.call_args.kwargs["params"]["dataProviders"] == ["APXMIDP"]
