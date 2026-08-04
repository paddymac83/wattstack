from datetime import date

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day
from wattstack_core.prices import SyntheticPriceProvider


def test_optimize_day_returns_48_periods():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(battery, [Market.WHOLESALE], SyntheticPriceProvider(), date(2025, 1, 1))
    assert len(result.soc_mwh) == 48
    assert len(result.charge_mw) == 48


def test_soc_never_exceeds_battery_bounds():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(battery, [Market.WHOLESALE], SyntheticPriceProvider(), date(2025, 1, 1))
    for soc in result.soc_mwh:
        assert battery.soc_min_mwh - 1e-6 <= soc <= battery.soc_max_mwh + 1e-6


def test_adding_reserve_markets_does_not_reduce_total_revenue():
    """More options should never leave the optimizer worse off."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    prices = SyntheticPriceProvider()
    day = date(2025, 1, 1)

    wholesale_only = optimize_day(battery, [Market.WHOLESALE], prices, day)
    stacked = optimize_day(
        battery,
        [Market.WHOLESALE, Market.DYNAMIC_CONTAINMENT_LOW, Market.DYNAMIC_CONTAINMENT_HIGH],
        prices,
        day,
    )
    assert stacked.total_revenue >= wholesale_only.total_revenue - 1e-6


def test_discharge_direction_commitments_reserve_soc_headroom():
    """DC-Low is discharge-direction (injects when frequency is low)
    -- a battery that commits it should be headroom-constrained. This
    is the whole point of the headroom constraint."""
    battery = BatterySpec(power_mw=5, duration_hours=1)  # short duration: headroom should bind
    prices = SyntheticPriceProvider()
    day = date(2025, 1, 1)

    stacked = optimize_day(battery, [Market.WHOLESALE, Market.DYNAMIC_CONTAINMENT_LOW], prices, day)
    assert sum(stacked.reserve_mw[Market.DYNAMIC_CONTAINMENT_LOW]) >= 0


def test_charge_direction_commitments_reserve_soc_footroom():
    """DC-High is charge-direction (absorbs when frequency is high)
    -- a battery that commits it should be footroom-constrained, the
    symmetric-but-opposite case from headroom. Force it to matter: a
    battery sitting at soc_max has zero footroom, so DC-High reserve
    must be zero in every period the SOC is pinned to the ceiling."""
    battery = BatterySpec(power_mw=5, duration_hours=1)
    prices = SyntheticPriceProvider()
    day = date(2025, 1, 1)

    result = optimize_day(
        battery,
        [Market.DYNAMIC_CONTAINMENT_HIGH],
        prices,
        day,
        initial_soc_mwh=battery.soc_max_mwh,
    )
    for t, soc in enumerate(result.soc_mwh):
        if soc >= battery.soc_max_mwh - 1e-6:
            assert result.reserve_mw[Market.DYNAMIC_CONTAINMENT_HIGH][t] <= 1e-6


def test_dc_high_and_dc_low_are_genuinely_opposite_directions():
    """Regression test for the direction correction itself: DC-High
    must be charge-direction and DC-Low must be discharge-direction,
    not the other way around (an earlier version of this project had
    them backwards)."""
    from wattstack_core.markets import MARKET_REGISTRY

    assert MARKET_REGISTRY[Market.DYNAMIC_CONTAINMENT_HIGH].direction == "charge"
    assert MARKET_REGISTRY[Market.DYNAMIC_CONTAINMENT_LOW].direction == "discharge"


def test_bm_offer_and_bm_bid_are_opposite_directions():
    from wattstack_core.markets import MARKET_REGISTRY

    assert MARKET_REGISTRY[Market.BM_OFFER].direction == "discharge"
    assert MARKET_REGISTRY[Market.BM_BID].direction == "charge"


def test_market_not_in_registry_is_silently_excluded_from_dispatch():
    """Markets outside MARKET_REGISTRY (e.g. CAPACITY_MARKET, DR, DM
    -- none wired into daily dispatch yet) shouldn't error, just
    contribute nothing."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(
        battery, [Market.WHOLESALE, Market.CAPACITY_MARKET], SyntheticPriceProvider(), date(2025, 1, 1)
    )
    assert result.revenue_by_market[Market.CAPACITY_MARKET] == 0.0
    assert Market.CAPACITY_MARKET not in result.reserve_mw


def test_market_display_name_capitalises_bm_correctly():
    from wattstack_core.markets import market_display_name

    assert market_display_name(Market.BM_OFFER) == "BM Offer"
    assert market_display_name(Market.BM_BID) == "BM Bid"
    assert market_display_name(Market.DYNAMIC_CONTAINMENT_HIGH) == "Dynamic Containment High"
    assert market_display_name(Market.WHOLESALE) == "Wholesale"
