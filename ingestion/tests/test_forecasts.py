"""No real network calls -- requests.get is mocked throughout."""
from datetime import datetime
from unittest.mock import MagicMock, patch

from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.forecasts import ElexonDemandForecastProvider, ElexonWindForecastProvider, ForecastProvider


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def test_elexon_demand_forecast_provider_satisfies_the_protocol():
    """Structural typing, checked directly rather than assumed --
    @runtime_checkable on ForecastProvider makes this a real
    assertion, not just a comment claiming it's true."""
    provider = ElexonDemandForecastProvider()
    assert isinstance(provider, ForecastProvider)


@patch("wattstack_ingestion.elexon.requests.get")
def test_as_of_calls_the_history_endpoint_with_the_given_publish_time(mock_get):
    """The core design property: as_of() always goes through the
    history endpoint with exactly the publish_time it was given --
    no special-casing "now" vs a historical time. This is what makes
    a live run and a backtest run the same code path."""
    row = {"settlementDate": "2026-08-01", "demand": 25000}
    mock_get.return_value = _mock_response([row])

    provider = ElexonDemandForecastProvider(client=ElexonClient())
    publish_time = datetime(2026, 7, 31, 9, 0, 0)
    result = provider.as_of(publish_time)

    assert result == [row]
    assert mock_get.call_args.kwargs["params"]["publishTime"] == "2026-07-31T09:00:00"


@patch("wattstack_ingestion.elexon.requests.get")
def test_as_of_called_with_now_uses_the_same_history_endpoint(mock_get):
    """Explicitly proves "now" isn't special-cased to a different
    endpoint -- a live run and a backtest genuinely share one code
    path, not just in theory."""
    mock_get.return_value = _mock_response([{"demand": 25000}])

    provider = ElexonDemandForecastProvider(client=ElexonClient())
    provider.as_of(datetime.now())

    called_url = mock_get.call_args.args[0]
    assert "/forecast/demand/day-ahead/history" in called_url


def test_provider_defaults_to_a_real_elexon_client_when_none_given():
    provider = ElexonDemandForecastProvider()
    assert isinstance(provider.client, ElexonClient)


def test_elexon_wind_forecast_provider_satisfies_the_protocol():
    """Wind genuinely fits ForecastProvider, unlike LOLPDRM (ADR
    0010) -- checked directly via isinstance, not assumed."""
    provider = ElexonWindForecastProvider()
    assert isinstance(provider, ForecastProvider)


@patch("wattstack_ingestion.elexon.requests.get")
def test_wind_as_of_calls_the_history_endpoint_with_the_given_publish_time(mock_get):
    row = {"settlementDate": "2026-08-01", "generation": 8500}
    mock_get.return_value = _mock_response([row])

    provider = ElexonWindForecastProvider(client=ElexonClient())
    publish_time = datetime(2026, 7, 31, 8, 30, 0)
    result = provider.as_of(publish_time)

    assert result == [row]
    assert mock_get.call_args.kwargs["params"]["publishTime"] == "2026-07-31T08:30:00"


@patch("wattstack_ingestion.elexon.requests.get")
def test_wind_as_of_called_with_now_uses_the_same_history_endpoint(mock_get):
    mock_get.return_value = _mock_response([{"generation": 8500}])

    provider = ElexonWindForecastProvider(client=ElexonClient())
    provider.as_of(datetime.now())

    called_url = mock_get.call_args.args[0]
    assert "/forecast/generation/wind/history" in called_url
