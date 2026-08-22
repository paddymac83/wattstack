"""No real network calls -- requests.get is mocked throughout."""
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from wattstack_ingestion.neso import NesoClient
from wattstack_ingestion.prices import NesoDCPriceProvider


class _FakeMarket:
    """Stand-in for core.markets.Market -- ingestion doesn't import
    core (ADR 0009), so NesoDCPriceProvider dispatches on `.name`
    structurally. This proves that works without ever importing the
    real enum."""
    def __init__(self, name):
        self.name = name


DC_HIGH = _FakeMarket("DYNAMIC_CONTAINMENT_HIGH")
DC_LOW = _FakeMarket("DYNAMIC_CONTAINMENT_LOW")
WHOLESALE = _FakeMarket("WHOLESALE")


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _row(delivery_start, auction_product, price):
    """auctionProduct, confirmed live to be the field holding
    "DCH"/"DCL" -- NOT serviceType, which holds a broader, unrelated
    category ("Response", "Slow Reserve")."""
    return {"deliveryStart": delivery_start, "auctionProduct": auction_product, "clearingPrice": price}


def test_satisfies_reserve_prices_shape():
    provider = NesoDCPriceProvider(client=NesoClient())
    assert hasattr(provider, "reserve_prices")
    assert not hasattr(provider, "wholesale_prices")  # deliberately partial, stated in the docstring


def test_raises_for_any_market_other_than_dc_high_or_dc_low():
    provider = NesoDCPriceProvider(client=NesoClient())
    try:
        provider.reserve_prices(date(2026, 6, 15), WHOLESALE)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "DC-High/DC-Low" in str(e)


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_returns_48_values(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        _row("2026-06-01T08:00:00", "DCH", 12.0),
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert len(prices) == 48


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_filters_by_auction_product(mock_get):
    """DC-Low rows must never leak into a DC-High request, and vice
    versa -- both services share the same dataset."""
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        _row("2026-06-01T08:00:00", "DCH", 100.0),
        _row("2026-06-01T08:00:00", "DCL", 50.0),
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)

    high_prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    low_prices = provider.reserve_prices(date(2026, 6, 15), DC_LOW)

    period = 17  # hour 8 -> period 17
    assert high_prices[period - 1] == 100.0
    assert low_prices[period - 1] == 50.0


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_ignores_service_type_entirely(mock_get):
    """serviceType ("Response", "Slow Reserve") doesn't distinguish
    DC-High from DC-Low at all -- confirmed live. A row must be
    matched purely on auctionProduct, regardless of what serviceType
    says, including when serviceType is something unrelated or
    missing entirely."""
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"deliveryStart": "2026-06-01T08:00:00", "auctionProduct": "DCH",
         "serviceType": "Slow Reserve", "clearingPrice": 100.0},
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert prices[16] == 100.0  # matched via auctionProduct alone


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_broadcasts_efa_block_average_across_its_settlement_periods(mock_get):
    """A single EFA block (e.g. 07:00-11:00, block 3) covers 8
    settlement periods (15-22) -- the same averaged price must appear
    in all 8, not just the one row's own period."""
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        _row("2026-06-01T08:00:00", "DCH", 100.0),  # hour 8 -> EFA block 3 (07:00-11:00)
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    block_3_periods = prices[14:22]  # periods 15-22 (0-indexed 14:22), hours 7-10
    assert all(p == 100.0 for p in block_3_periods)
    assert prices[0] == 0.0  # period 1 (hour 0, block 1) has no data -- falls back to 0.0


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_averages_across_multiple_days_in_the_same_efa_block(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        _row("2026-06-01T08:00:00", "DCH", 100.0),
        _row("2026-06-05T08:30:00", "DCH", 200.0),  # same EFA block (hour 8), different day
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert prices[16] == 150.0  # period 17 (hour 8) -- (100+200)/2


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_excludes_rows_outside_the_lookback_window(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        _row("2026-01-01T08:00:00", "DCH", 999.0),  # way outside a 30-day lookback from June 15
        _row("2026-06-10T08:00:00", "DCH", 100.0),  # inside the window
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert prices[16] == 100.0  # not affected by the 999.0 outlier from outside the window


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_excludes_the_target_day_itself(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        _row("2026-06-15T08:00:00", "DCH", 999.0),  # the target day itself -- must be excluded
        _row("2026-06-10T08:00:00", "DCH", 100.0),
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert prices[16] == 100.0  # not 999.0 or an average including it


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_uses_sort_by_delivery_start_desc(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": []}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert mock_get.call_args.kwargs["params"]["sort"] == "deliveryStart desc"


@patch("wattstack_ingestion.neso.requests.get")
def test_reserve_prices_handles_malformed_or_missing_rows_gracefully(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"deliveryStart": "not-a-date", "auctionProduct": "DCH", "clearingPrice": 100.0},
        {"deliveryStart": "2026-06-10T08:00:00", "auctionProduct": "DCH", "clearingPrice": None},
        {"deliveryStart": "2026-06-10T08:00:00", "auctionProduct": "DCH"},  # missing price entirely
    ]}})
    provider = NesoDCPriceProvider(client=NesoClient(), lookback_days=30)
    prices = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert prices == [0.0] * 48  # every row dropped -- no crash, clean fallback


def test_field_names_are_configurable_not_hardcoded():
    provider = NesoDCPriceProvider(
        auction_product_field="product", price_field="price", delivery_start_field="start",
        dc_high_value="HIGH", dc_low_value="LOW",
    )
    assert provider.auction_product_field == "product"
    assert provider.price_field == "price"
    assert provider.delivery_start_field == "start"
    assert provider.dc_high_value == "HIGH"
    assert provider.dc_low_value == "LOW"


def test_default_field_names_match_the_confirmed_live_values():
    """auctionProduct/DCH/DCL confirmed live -- these are no longer
    reasoned guesses the way they were before, unlike
    ElexonWholesalePriceProvider's period_field/price_field, which
    still are."""
    provider = NesoDCPriceProvider()
    assert provider.auction_product_field == "auctionProduct"
    assert provider.price_field == "clearingPrice"
    assert provider.delivery_start_field == "deliveryStart"
    assert provider.dc_high_value == "DCH"
    assert provider.dc_low_value == "DCL"


def test_service_type_field_no_longer_exists_as_a_parameter():
    """Confirmed live that serviceType doesn't distinguish DC-High
    from DC-Low -- the parameter was removed entirely rather than
    kept unused, since an unused parameter that looks configurable is
    worse than no parameter."""
    assert not hasattr(NesoDCPriceProvider(), "service_type_field")


# --- dc_activation_probability ---


def test_dc_activation_probability_returns_48_flat_values():
    provider = NesoDCPriceProvider(activation_probability=0.05)
    result = provider.dc_activation_probability(date(2026, 6, 15), DC_HIGH)
    assert result == [0.05] * 48


def test_dc_activation_probability_same_value_for_both_dc_markets():
    """A single, flat parameter -- not calibrated per-market, and
    honest about that rather than implying an asymmetry that doesn't
    exist here."""
    provider = NesoDCPriceProvider(activation_probability=0.03)
    high_result = provider.dc_activation_probability(date(2026, 6, 15), DC_HIGH)
    low_result = provider.dc_activation_probability(date(2026, 6, 15), DC_LOW)
    assert high_result == low_result == [0.03] * 48


def test_dc_activation_probability_raises_for_non_dc_market():
    provider = NesoDCPriceProvider()
    try:
        provider.dc_activation_probability(date(2026, 6, 15), WHOLESALE)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "DC-High/DC-Low" in str(e)


def test_default_activation_probability_is_a_stated_small_constant():
    """0.02, not calibrated from anything real -- same honesty as
    ElexonBMPriceProvider.acceptance_derating and
    ElexonImbalancePriceProvider's own derating factor."""
    provider = NesoDCPriceProvider()
    assert provider.activation_probability == 0.02


def test_activation_probability_is_configurable_not_hardcoded():
    provider = NesoDCPriceProvider(activation_probability=0.1)
    assert provider.activation_probability == 0.1
