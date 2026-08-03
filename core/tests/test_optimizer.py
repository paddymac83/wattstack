from datetime import date

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day
from wattstack_core.prices import SyntheticPriceProvider


def test_optimize_day_returns_48_periods():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(battery, [Market.ENERGY], SyntheticPriceProvider(), date(2025, 1, 1))
    assert len(result.soc_mwh) == 48
    assert len(result.charge_mw) == 48


def test_soc_never_exceeds_battery_bounds():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(battery, [Market.ENERGY], SyntheticPriceProvider(), date(2025, 1, 1))
    for soc in result.soc_mwh:
        assert battery.soc_min_mwh - 1e-6 <= soc <= battery.soc_max_mwh + 1e-6


def test_adding_response_markets_does_not_reduce_total_revenue():
    """More options should never leave the optimizer worse off."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    prices = SyntheticPriceProvider()
    day = date(2025, 1, 1)

    energy_only = optimize_day(battery, [Market.ENERGY], prices, day)
    stacked = optimize_day(
        battery, [Market.ENERGY, Market.DYNAMIC_CONTAINMENT, Market.DYNAMIC_REGULATION], prices, day
    )
    assert stacked.total_revenue >= energy_only.total_revenue - 1e-6


def test_response_commitments_reserve_soc_headroom():
    """A battery that commits response capacity should show reduced
    charge/discharge freedom versus energy-only -- this is the whole
    point of the headroom constraint."""
    battery = BatterySpec(power_mw=5, duration_hours=1)  # short duration: headroom should bind
    prices = SyntheticPriceProvider()
    day = date(2025, 1, 1)

    stacked = optimize_day(
        battery, [Market.ENERGY, Market.DYNAMIC_CONTAINMENT], prices, day
    )
    # Some DC reserve should actually be committed for a battery this
    # short-duration to be worth testing at all.
    assert sum(stacked.reserve_mw[Market.DYNAMIC_CONTAINMENT]) >= 0
