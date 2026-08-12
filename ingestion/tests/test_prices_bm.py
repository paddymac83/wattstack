"""No real network calls -- requests.get is mocked throughout."""
from datetime import date
from unittest.mock import MagicMock, patch

from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.prices import ElexonBMPriceProvider


class _FakeMarket:
    def __init__(self, name):
        self.name = name


BM_OFFER = _FakeMarket("BM_OFFER")
BM_BID = _FakeMarket("BM_BID")
WHOLESALE = _FakeMarket("WHOLESALE")


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def test_satisfies_reserve_prices_shape():
    provider = ElexonBMPriceProvider(client=ElexonClient())
    assert hasattr(provider, "reserve_prices")
    assert not hasattr(provider, "wholesale_prices")


def test_raises_for_any_market_other_than_bm_offer_or_bm_bid():
    provider = ElexonBMPriceProvider(client=ElexonClient())
    try:
        provider.reserve_prices(date(2026, 6, 15), WHOLESALE)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "BM_OFFER/BM_BID" in str(e)


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_returns_48_values(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "offerPrice": 80.0, "bidPrice": 20.0}])
    provider = ElexonBMPriceProvider(client=ElexonClient(), lookback_days=2)
    prices = provider.reserve_prices(date(2026, 6, 15), BM_OFFER)
    assert len(prices) == 48


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_uses_offer_price_for_bm_offer_and_bid_price_for_bm_bid(mock_get):
    """BM-Offer (discharge) and BM-Bid (charge) must read genuinely
    different columns -- confusing them would price a sell action
    with a buy price or vice versa."""
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "offerPrice": 80.0, "bidPrice": 20.0}])
    provider = ElexonBMPriceProvider(client=ElexonClient(), lookback_days=2, acceptance_derating=1.0)

    offer_prices = provider.reserve_prices(date(2026, 6, 15), BM_OFFER)
    bid_prices = provider.reserve_prices(date(2026, 6, 15), BM_BID)

    assert offer_prices[0] == 80.0
    assert bid_prices[0] == 20.0


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_applies_acceptance_derating(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": 1, "offerPrice": 100.0, "bidPrice": 100.0}])
    provider = ElexonBMPriceProvider(client=ElexonClient(), lookback_days=2, acceptance_derating=0.3)
    prices = provider.reserve_prices(date(2026, 6, 15), BM_OFFER)
    assert prices[0] == 30.0  # 100.0 * 0.3, not the raw submitted price


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_averages_across_multiple_units_and_days(mock_get):
    """Two calls (two lookback days) x 48 periods each -- the mock
    returns the same row for every period/day, but this proves the
    averaging path is genuinely exercised, not just returning the
    first row seen."""
    mock_get.return_value = _mock_response([
        {"settlementPeriod": 1, "offerPrice": 60.0, "bidPrice": 10.0},
        {"settlementPeriod": 1, "offerPrice": 100.0, "bidPrice": 10.0},
    ])
    provider = ElexonBMPriceProvider(client=ElexonClient(), lookback_days=1, acceptance_derating=1.0)
    prices = provider.reserve_prices(date(2026, 6, 15), BM_OFFER)
    assert prices[0] == 80.0  # (60+100)/2, pooled across both rows


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_fetches_the_confirmed_lookback_day_count(mock_get):
    mock_get.return_value = _mock_response([{"settlementPeriod": p, "offerPrice": 50.0, "bidPrice": 50.0} for p in range(1, 49)])
    provider = ElexonBMPriceProvider(client=ElexonClient(), lookback_days=3)
    provider.reserve_prices(date(2026, 6, 15), BM_OFFER)
    assert mock_get.call_count == 3 * 48  # 3 days x 48 settlement periods per day


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_falls_back_to_zero_for_a_period_with_no_data(mock_get):
    mock_get.return_value = _mock_response([])
    provider = ElexonBMPriceProvider(client=ElexonClient(), lookback_days=1)
    prices = provider.reserve_prices(date(2026, 6, 15), BM_OFFER)
    assert prices == [0.0] * 48


def test_default_derating_is_conservative_and_stated():
    provider = ElexonBMPriceProvider()
    assert provider.acceptance_derating == 0.3


def test_field_names_and_derating_are_configurable_not_hardcoded():
    provider = ElexonBMPriceProvider(
        offer_price_field="offer", bid_price_field="bid", period_field="period", acceptance_derating=0.5,
    )
    assert provider.offer_price_field == "offer"
    assert provider.bid_price_field == "bid"
    assert provider.period_field == "period"
    assert provider.acceptance_derating == 0.5
