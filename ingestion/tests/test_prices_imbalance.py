"""No real network calls -- requests.get is mocked throughout."""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.forecasts import ElexonDemandForecastProvider
from wattstack_ingestion.prices import ElexonImbalancePriceProvider


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
    provider = ElexonImbalancePriceProvider(client=ElexonClient())
    assert hasattr(provider, "reserve_prices")
    assert not hasattr(provider, "wholesale_prices")


def test_raises_for_any_market_other_than_bm_offer_or_bm_bid():
    provider = ElexonImbalancePriceProvider(client=ElexonClient())
    try:
        provider.reserve_prices(date(2026, 6, 15), WHOLESALE)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "BM_OFFER/BM_BID" in str(e)


def _fake_get_perfectly_separable(url, params=None, timeout=30):
    """demand=28150 always Long/£40, demand=30450 always Short/£90 --
    same shape as the notebook's own perfectly-separable validation
    mock, non-coincidental demand values to avoid bin_counts_by_group's
    documented exact-boundary clamping behaviour."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "forecast/demand/day-ahead/history" in url:
        resp.json.return_value = [
            {"settlementPeriod": 1, "demand": 28150}, {"settlementPeriod": 2, "demand": 30450},
        ]
    elif "system-prices" in url:
        day = url.rsplit("/", 1)[-1]
        resp.json.return_value = [
            {"settlementDate": day, "settlementPeriod": 1, "systemSellPrice": 40.0,
             "systemBuyPrice": 40.0, "netImbalanceVolume": -100.0},
            {"settlementDate": day, "settlementPeriod": 2, "systemSellPrice": 90.0,
             "systemBuyPrice": 90.0, "netImbalanceVolume": 200.0},
        ]
    else:
        resp.json.return_value = []
    return resp


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_returns_48_values(mock_get):
    mock_get.return_value = _mock_response([])
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_perfectly_separable):
        provider = ElexonImbalancePriceProvider(
            client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
            lookback_days=5, demand_bin_width=1000.0,
        )
        prices = provider.reserve_prices(date(2026, 6, 20), BM_OFFER)
    assert len(prices) == 48


@patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_perfectly_separable)
def test_reserve_prices_bm_offer_responds_only_to_short_probability(mock_get):
    """BM_OFFER = P(Short) x mean_price_given_short, with ~0 implicit
    contribution from Long periods -- period 1 (Long, P(Short)=0 for
    its bucket, given the deterministic mock's clean separation)
    should forecast ~0, not the old blended 40.0; period 2 (Short,
    P(Short)=1) should forecast the full mean_short (90.0).

    derating_factor=1.0 explicitly -- this test is about the regime
    logic specifically, not the derating multiplier (covered by its
    own dedicated tests below)."""
    provider = ElexonImbalancePriceProvider(
        client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
        lookback_days=10, demand_bin_width=1000.0, derating_factor=1.0,
    )
    prices = provider.reserve_prices(date(2026, 6, 20), BM_OFFER)
    assert prices[0] == 0.0    # period 1 -- Long -- BM_OFFER isn't valuable here
    assert prices[1] == 90.0   # period 2 -- Short -- BM_OFFER's own regime, full value


@patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_perfectly_separable)
def test_reserve_prices_bm_bid_responds_only_to_long_probability(mock_get):
    """The mirror image of the BM_OFFER test: BM_BID = P(Long) x
    mean_price_given_long, ~0 contribution from Short periods."""
    provider = ElexonImbalancePriceProvider(
        client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
        lookback_days=10, demand_bin_width=1000.0, derating_factor=1.0,
    )
    prices = provider.reserve_prices(date(2026, 6, 20), BM_BID)
    assert prices[0] == 40.0   # period 1 -- Long -- BM_BID's own regime, full value
    assert prices[1] == 0.0    # period 2 -- Short -- BM_BID isn't valuable here


@patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_perfectly_separable)
def test_reserve_prices_bm_offer_and_bm_bid_genuinely_differ(mock_get):
    """The actual point of this change: the two markets must no
    longer receive the identical forecast -- a real, deliberate
    asymmetry, not the previous shared blend."""
    provider = ElexonImbalancePriceProvider(
        client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
        lookback_days=10, demand_bin_width=1000.0,
    )
    offer_prices = provider.reserve_prices(date(2026, 6, 20), BM_OFFER)
    bid_prices = provider.reserve_prices(date(2026, 6, 20), BM_BID)
    assert offer_prices != bid_prices
    # and specifically: whichever regime a period favours, that market should dominate
    assert offer_prices[1] > bid_prices[1]   # period 2 is Short -- BM_OFFER should be worth more
    assert bid_prices[0] > offer_prices[0]   # period 1 is Long -- BM_BID should be worth more


@patch("wattstack_ingestion.elexon.requests.get")
def test_reserve_prices_falls_back_to_zero_when_no_training_data(mock_get):
    mock_get.return_value = _mock_response([])  # every call returns nothing at all
    provider = ElexonImbalancePriceProvider(
        client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
        lookback_days=3,
    )
    prices = provider.reserve_prices(date(2026, 6, 20), BM_OFFER)
    assert prices == [0.0] * 48  # no data anywhere -- the honest zero fallback


def _fake_get_missing_period_2_demand(url, params=None, timeout=30):
    """Training data has a clean, known 50/50 Short/Long split;
    the TARGET day's forecast is only given for period 1, not
    period 2 -- exercising the new overall_p_short/overall_p_long
    fallback (distinct from the all-data-missing zero fallback)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "forecast/demand/day-ahead/history" in url:
        pt = params.get("publishTime", "")
        if "2026-06-19" in pt:  # the target day's own trigger -- period 2 deliberately absent
            resp.json.return_value = [{"settlementPeriod": 1, "demand": 28150}]
        else:  # historical training days -- both periods present, clean 50/50 split
            resp.json.return_value = [{"settlementPeriod": 1, "demand": 28150}, {"settlementPeriod": 2, "demand": 30450}]
    elif "system-prices" in url:
        day = url.rsplit("/", 1)[-1]
        resp.json.return_value = [
            {"settlementDate": day, "settlementPeriod": 1, "systemSellPrice": 40.0,
             "systemBuyPrice": 40.0, "netImbalanceVolume": -100.0},
            {"settlementDate": day, "settlementPeriod": 2, "systemSellPrice": 90.0,
             "systemBuyPrice": 90.0, "netImbalanceVolume": 200.0},
        ]
    else:
        resp.json.return_value = []
    return resp


@patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_missing_period_2_demand)
def test_reserve_prices_period_with_missing_demand_uses_overall_rate_not_zero(mock_get):
    provider = ElexonImbalancePriceProvider(
        client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
        lookback_days=5, demand_bin_width=1000.0, derating_factor=1.0,
    )
    prices = provider.reserve_prices(date(2026, 6, 20), BM_OFFER)
    # period 2's own demand is missing from the target forecast, but training data exists (50% Short overall)
    # -- BM_OFFER should reflect that overall rate, not silently fall back to 0.0
    assert prices[1] == 0.5 * 90.0  # overall_p_short (0.5) x mean_short (90.0)


# --- derating_factor / acceptance_probability ---


@patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_perfectly_separable)
def test_default_derating_factor_is_applied_to_the_forecast(mock_get):
    provider = ElexonImbalancePriceProvider(
        client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
        lookback_days=10, demand_bin_width=1000.0,
    )
    prices = provider.reserve_prices(date(2026, 6, 20), BM_OFFER)
    assert prices[1] == pytest.approx(90.0 * 0.3)  # default derating_factor


@patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_perfectly_separable)
def test_custom_derating_factor_is_applied_to_the_forecast(mock_get):
    provider = ElexonImbalancePriceProvider(
        client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
        lookback_days=10, demand_bin_width=1000.0, derating_factor=0.6,
    )
    prices = provider.reserve_prices(date(2026, 6, 20), BM_OFFER)
    assert prices[1] == pytest.approx(90.0 * 0.6)


def test_default_derating_factor_matches_the_old_providers_starting_point():
    provider = ElexonImbalancePriceProvider()
    assert provider.derating_factor == 0.3


def test_acceptance_probability_returns_derating_factor_flat_across_all_periods():
    provider = ElexonImbalancePriceProvider(derating_factor=0.45)
    probs = provider.acceptance_probability(date(2026, 6, 20), BM_OFFER)
    assert len(probs) == 48
    assert all(p == 0.45 for p in probs)


def test_acceptance_probability_raises_for_unsupported_markets():
    provider = ElexonImbalancePriceProvider()
    try:
        provider.acceptance_probability(date(2026, 6, 20), WHOLESALE)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "BM_OFFER/BM_BID" in str(e)


def test_acceptance_probability_and_reserve_prices_derating_use_the_same_number():
    """The whole point of sharing derating_factor between the two
    methods: they must never be independently tunable in a way that
    could drift out of sync."""
    provider = ElexonImbalancePriceProvider(derating_factor=0.55)
    assert provider.acceptance_probability(date(2026, 6, 20), BM_OFFER)[0] == provider.derating_factor


@patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get_perfectly_separable)
def test_reserve_prices_uses_the_day_ahead_trigger_convention(mock_get):
    """Both training history and the target day's own forecast must
    use the confirmed 10:00 UTC day-before trigger -- proven by
    checking the actual publishTime values sent, not just that the
    call succeeds."""
    call_log = []
    original_side_effect = _fake_get_perfectly_separable

    def _logging_fake_get(url, params=None, timeout=30):
        if "publishTime" in (params or {}):
            call_log.append(params["publishTime"])
        return original_side_effect(url, params=params, timeout=timeout)

    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_logging_fake_get):
        provider = ElexonImbalancePriceProvider(
            client=ElexonClient(), forecast_provider=ElexonDemandForecastProvider(client=ElexonClient()),
            lookback_days=2,
        )
        provider.reserve_prices(date(2026, 6, 20), BM_OFFER)

    assert "2026-06-19T10:00:00+00:00" in call_log  # trigger for the target day itself
    assert "2026-06-18T10:00:00+00:00" in call_log  # trigger for 1 day back
    assert "2026-06-17T10:00:00+00:00" in call_log  # trigger for 2 days back


def test_field_names_are_configurable_not_hardcoded():
    provider = ElexonImbalancePriceProvider(
        period_field="period", demand_field="fc_demand", price_field="ssp", niv_field="niv",
    )
    assert provider.period_field == "period"
    assert provider.demand_field == "fc_demand"
    assert provider.price_field == "ssp"
    assert provider.niv_field == "niv"


def test_default_field_names_and_lookback_match_what_was_validated():
    provider = ElexonImbalancePriceProvider()
    assert provider.period_field == "settlementPeriod"
    assert provider.demand_field == "demand"
    assert provider.price_field == "systemSellPrice"
    assert provider.niv_field == "netImbalanceVolume"
    assert provider.lookback_days == 60
    assert provider.demand_bin_width == 1000.0
