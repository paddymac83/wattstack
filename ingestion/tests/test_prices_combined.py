"""No real network calls -- delegate providers are simple fakes."""
from datetime import date

from wattstack_ingestion.prices import CombinedPriceProvider


class _FakeWholesaleProvider:
    def __init__(self, prices):
        self._prices = prices
        self.calls = []

    def wholesale_prices(self, day):
        self.calls.append(day)
        return self._prices


class _FakeReserveProvider:
    """Mirrors the real providers' self-declaring behaviour: raises
    ValueError for any market it doesn't cover, rather than silently
    returning something wrong."""
    def __init__(self, covered_markets, prices):
        self.covered_markets = covered_markets
        self._prices = prices
        self.calls = []

    def reserve_prices(self, day, market):
        market_name = getattr(market, "name", str(market))
        if market_name not in self.covered_markets:
            raise ValueError(f"does not cover {market_name!r}")
        self.calls.append((day, market))
        return self._prices


class _FakeMarket:
    def __init__(self, name):
        self.name = name


DC_HIGH = _FakeMarket("DYNAMIC_CONTAINMENT_HIGH")
BM_OFFER = _FakeMarket("BM_OFFER")
WHOLESALE = _FakeMarket("WHOLESALE")


def test_satisfies_the_full_price_provider_shape():
    provider = CombinedPriceProvider(
        wholesale_provider=_FakeWholesaleProvider([1.0] * 48),
        reserve_providers=[_FakeReserveProvider(["DYNAMIC_CONTAINMENT_HIGH"], [2.0] * 48)],
    )
    assert hasattr(provider, "wholesale_prices")
    assert hasattr(provider, "reserve_prices")


def test_wholesale_prices_delegates_to_the_wholesale_provider():
    wholesale = _FakeWholesaleProvider([42.0] * 48)
    provider = CombinedPriceProvider(wholesale_provider=wholesale, reserve_providers=[])
    result = provider.wholesale_prices(date(2026, 6, 15))
    assert result == [42.0] * 48
    assert wholesale.calls == [date(2026, 6, 15)]


def test_reserve_prices_routes_to_the_provider_that_covers_the_market():
    """The actual point of the plural reserve_providers list: a DC
    provider and a BM provider both present, each request must reach
    the one that actually covers it."""
    dc_provider = _FakeReserveProvider(["DYNAMIC_CONTAINMENT_HIGH", "DYNAMIC_CONTAINMENT_LOW"], [10.0] * 48)
    bm_provider = _FakeReserveProvider(["BM_OFFER", "BM_BID"], [20.0] * 48)
    provider = CombinedPriceProvider(
        wholesale_provider=_FakeWholesaleProvider([0.0] * 48),
        reserve_providers=[dc_provider, bm_provider],
    )

    dc_result = provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    bm_result = provider.reserve_prices(date(2026, 6, 15), BM_OFFER)

    assert dc_result == [10.0] * 48
    assert bm_result == [20.0] * 48
    assert len(dc_provider.calls) == 1
    assert len(bm_provider.calls) == 1


def test_reserve_prices_raises_clearly_when_no_provider_covers_the_market():
    dc_provider = _FakeReserveProvider(["DYNAMIC_CONTAINMENT_HIGH"], [10.0] * 48)
    provider = CombinedPriceProvider(wholesale_provider=_FakeWholesaleProvider([0.0] * 48), reserve_providers=[dc_provider])
    try:
        provider.reserve_prices(date(2026, 6, 15), BM_OFFER)  # no BM provider registered
        assert False, "expected ValueError"
    except ValueError as e:
        assert "BM_OFFER" in str(e)


def test_wholesale_and_reserve_calls_are_independent():
    """Calling one method must never touch an unrelated provider --
    proves this is a pure router, not something that couples
    providers together."""
    wholesale = _FakeWholesaleProvider([1.0] * 48)
    dc_provider = _FakeReserveProvider(["DYNAMIC_CONTAINMENT_HIGH"], [2.0] * 48)
    provider = CombinedPriceProvider(wholesale_provider=wholesale, reserve_providers=[dc_provider])

    provider.wholesale_prices(date(2026, 6, 15))
    assert len(wholesale.calls) == 1
    assert len(dc_provider.calls) == 0

    provider.reserve_prices(date(2026, 6, 15), DC_HIGH)
    assert len(wholesale.calls) == 1  # unchanged
    assert len(dc_provider.calls) == 1
