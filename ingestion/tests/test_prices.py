"""No real network calls -- requests.get is mocked throughout."""
from datetime import date
from unittest.mock import MagicMock, patch

from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.prices import ElexonWholesalePriceProvider


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def test_satisfies_wholesale_prices_shape():
    """Structural check against core's PriceProvider shape (day ->
    list[float], length 48) without importing core -- this project's
    standing rule (ADR 0009) is that ingestion doesn't depend on core,
    so this checks duck-typed behaviour directly, not via isinstance
    against a Protocol core defines."""
    provider = ElexonWholesalePriceProvider(client=ElexonClient())
    assert hasattr(provider, "wholesale_prices")
    assert not hasattr(provider, "reserve_prices")  # deliberately partial, stated in the docstring


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_returns_48_values(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "price": 55.0}])
    provider = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=2)
    prices = provider.wholesale_prices(date(2026, 6, 15))
    assert len(prices) == 48


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_chunks_long_lookback_into_7_day_requests(mock_get):
    """The real consequence of MID's confirmed 7-day range limit: a
    28-day lookback must become 4 chunked requests, not one."""
    mock_get.return_value = _mock_response([{"settlementPeriod": p, "price": 50.0} for p in range(1, 49)])
    provider = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=28)
    provider.wholesale_prices(date(2026, 6, 29))
    assert mock_get.call_count == 4


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_within_7_days_makes_a_single_request(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": p, "price": 50.0} for p in range(1, 49)])
    provider = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=5)
    provider.wholesale_prices(date(2026, 6, 15))
    assert mock_get.call_count == 1


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_averages_across_multiple_observations_of_the_same_period(mock_get):
    """Pooling within the one bulk response -- confirms the seasonal
    average is a genuine mean across every observation MID returned
    for that period, not just the first or last one."""
    mock_get.return_value = _mock_response([
        {"settlementPeriod": 1, "price": 40.0},
        {"settlementPeriod": 1, "price": 60.0},
    ])
    provider = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=2)
    prices = provider.wholesale_prices(date(2026, 6, 15))
    assert prices[0] == 50.0  # (40+60)/2


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_range_excludes_the_target_day_itself(mock_get):
    """The whole point of this provider: the fetched range must never
    include the target day, since that's exactly the data that
    doesn't exist yet at a pre-gate-closure trigger time."""
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "price": 50.0}])
    provider = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=3)
    provider.wholesale_prices(date(2026, 6, 15))
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["from"] == "2026-06-12T00:00:00"
    assert called_params["to"] == "2026-06-15T00:00:00"  # up to but not including the 15th


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_excludes_zero_values_from_the_average(mock_get):
    mock_get.return_value = _mock_response([
        {"settlementPeriod": 1, "price": 0.0},   # the known N2EX all-zero-date case
        {"settlementPeriod": 1, "price": 80.0},
    ])
    provider = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=2)
    prices = provider.wholesale_prices(date(2026, 6, 15))
    assert prices[0] == 80.0  # not 40.0 -- the zero observation is excluded, not averaged in


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_falls_back_to_zero_for_a_period_with_no_data(mock_get):
    mock_get.return_value = _mock_response([])  # nothing returned at all
    provider = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=1)
    prices = provider.wholesale_prices(date(2026, 6, 15))
    assert prices == [0.0] * 48


@patch("wattstack_ingestion.elexon.requests.get")
def test_wholesale_prices_passes_through_data_providers(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "price": 50.0}])
    provider = ElexonWholesalePriceProvider(
        client=ElexonClient(), lookback_days=1, data_providers=["N2EXMIDP", "APXMIDP"]
    )
    provider.wholesale_prices(date(2026, 6, 15))
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["dataProviders"] == ["N2EXMIDP", "APXMIDP"]


def test_default_data_provider_is_apx_not_n2ex():
    """Confirmed live: N2EXMIDP showed zero price/volume for the most
    recent several days, while APXMIDP for the identical dates showed
    real data -- rules out a general reporting lag (which would
    affect both equally) and points to an N2EX-specific feed issue.
    APXMIDP is the default because it's the one that actually works,
    not because of any assumption about which exchange is more
    liquid."""
    provider = ElexonWholesalePriceProvider()
    assert provider.data_providers == ["APXMIDP"]


def test_field_names_are_configurable_not_hardcoded():
    """MID's real field names aren't confirmed -- this proves they
    can be corrected without a code change, the actual point of
    making them constructor parameters."""
    provider = ElexonWholesalePriceProvider(period_field="period", price_field="mid_price")
    assert provider.period_field == "period"
    assert provider.price_field == "mid_price"


def test_default_field_names_are_the_documented_best_guess():
    provider = ElexonWholesalePriceProvider()
    assert provider.period_field == "settlementPeriod"
    assert provider.price_field == "price"
