from datetime import date

import pytest

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day, optimize_day_two_stage
from wattstack_core.prices import PERIODS_PER_DAY, SyntheticPriceProvider


class _FlatPriceProvider:
    """A simple, hand-calculable price provider -- flat prices, no
    diurnal shape, so expected values can be computed by hand rather
    than trusting the optimizer's own output."""

    def __init__(self, wholesale_price=50.0, dc_high_price=10.0, dc_low_price=9.0):
        self.wholesale_price = wholesale_price
        self.dc_high_price = dc_high_price
        self.dc_low_price = dc_low_price

    def wholesale_prices(self, day):
        return [self.wholesale_price] * PERIODS_PER_DAY

    def reserve_prices(self, day, market):
        price = self.dc_high_price if market == Market.DYNAMIC_CONTAINMENT_HIGH else self.dc_low_price
        return [price] * PERIODS_PER_DAY


# --- fixed_wholesale_mw ---


def test_fixed_wholesale_mw_and_wholesale_market_together_raises_error():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed = ([0.0] * PERIODS_PER_DAY, [0.0] * PERIODS_PER_DAY)
    with pytest.raises(ValueError, match="both fixed"):
        optimize_day(
            battery, [Market.WHOLESALE], SyntheticPriceProvider(), date(2025, 1, 1),
            fixed_wholesale_mw=fixed,
        )


def test_fixed_wholesale_mw_wrong_length_raises_error():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed = ([0.0] * 10, [0.0] * PERIODS_PER_DAY)  # charge list too short
    with pytest.raises(ValueError, match="48 values"):
        optimize_day(battery, [], SyntheticPriceProvider(), date(2025, 1, 1), fixed_wholesale_mw=fixed)


def test_fixed_wholesale_mw_exceeding_power_rating_raises_error():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed_charge = [0.0] * PERIODS_PER_DAY
    fixed_charge[10] = 999.0  # far exceeds 5 MW rating
    fixed = (fixed_charge, [0.0] * PERIODS_PER_DAY)
    with pytest.raises(ValueError, match="period 10"):
        optimize_day(battery, [], SyntheticPriceProvider(), date(2025, 1, 1), fixed_wholesale_mw=fixed)


def test_fixed_wholesale_mw_negative_value_raises_error():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed_discharge = [0.0] * PERIODS_PER_DAY
    fixed_discharge[3] = -1.0
    fixed = ([0.0] * PERIODS_PER_DAY, fixed_discharge)
    with pytest.raises(ValueError, match="period 3"):
        optimize_day(battery, [], SyntheticPriceProvider(), date(2025, 1, 1), fixed_wholesale_mw=fixed)


def test_fixed_wholesale_mw_result_matches_input_exactly():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed_charge = [0.0] * PERIODS_PER_DAY
    fixed_discharge = [0.0] * PERIODS_PER_DAY
    fixed_charge[5] = 1.0
    fixed_discharge[20] = 2.0
    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH], _FlatPriceProvider(), date(2025, 1, 1),
        fixed_wholesale_mw=(fixed_charge, fixed_discharge),
    )
    assert result.charge_mw == fixed_charge
    assert result.discharge_mw == fixed_discharge


def test_fixed_wholesale_mw_leaves_correct_remaining_power_for_reserve():
    """A 5MW battery with 3MW already fixed to wholesale charging in
    one period should have at most 2MW of headroom left for DC in
    that same period -- proven by making DC absurdly attractive and
    checking it doesn't exceed what's physically left."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed_charge = [0.0] * PERIODS_PER_DAY
    fixed_charge[5] = 3.0
    fixed_discharge = [0.0] * PERIODS_PER_DAY

    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_LOW], _FlatPriceProvider(dc_low_price=1000.0), date(2025, 1, 1),
        fixed_wholesale_mw=(fixed_charge, fixed_discharge),
    )
    assert result.reserve_mw[Market.DYNAMIC_CONTAINMENT_LOW][5] <= 2.0 + 1e-6


def test_fixed_wholesale_mw_produces_the_correct_soc_recursion():
    """Isolated single-period check, hand-calculated -- the same style
    as test_isolated_period_soc_drop_matches_the_expected_energy_formula_exactly
    in test_optimizer.py, applied to the fixed-schedule code path."""
    battery = BatterySpec(power_mw=5, duration_hours=2, round_trip_efficiency=1.0)  # eff=1.0 simplifies the hand-calc
    start_soc = (battery.soc_min_mwh + battery.soc_max_mwh) / 2

    fixed_charge = [0.0] * PERIODS_PER_DAY
    fixed_discharge = [0.0] * PERIODS_PER_DAY
    fixed_discharge[0] = 2.0  # discharge 2MW for 0.5h = 1.0 MWh, at eff=1.0 (sqrt(1.0)=1.0 one-way)

    result = optimize_day(
        battery, [], _FlatPriceProvider(), date(2025, 1, 1),
        fixed_wholesale_mw=(fixed_charge, fixed_discharge),
    )
    expected_soc_after_period_0 = start_soc - 2.0 * 0.5 / 1.0  # discharge_mw * dt / one_way_efficiency
    assert abs(result.soc_mwh[0] - expected_soc_after_period_0) < 1e-3


def test_fixed_wholesale_mw_revenue_matches_hand_calculation():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed_charge = [0.0] * PERIODS_PER_DAY
    fixed_discharge = [0.0] * PERIODS_PER_DAY
    fixed_discharge[0] = 2.0  # only period 0 has any wholesale activity

    result = optimize_day(
        battery, [], _FlatPriceProvider(wholesale_price=100.0), date(2025, 1, 1),
        fixed_wholesale_mw=(fixed_charge, fixed_discharge),
    )
    # (discharge - charge) * dt * price = (2.0 - 0.0) * 0.5 * 100.0 = 100.0
    assert result.revenue_by_market[Market.WHOLESALE] == 100.0


def test_wholesale_price_is_still_reported_when_fixed_even_though_not_a_free_market():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    fixed = ([0.0] * PERIODS_PER_DAY, [0.0] * PERIODS_PER_DAY)
    result = optimize_day(
        battery, [], _FlatPriceProvider(wholesale_price=42.0), date(2025, 1, 1), fixed_wholesale_mw=fixed
    )
    assert result.wholesale_price == [42.0] * PERIODS_PER_DAY  # not [] -- the price is a known, real input


# --- optimize_day_two_stage ---


def test_two_stage_rejects_wholesale_in_dc_markets():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    with pytest.raises(ValueError, match="must not include Market.WHOLESALE"):
        optimize_day_two_stage(
            battery, [Market.WHOLESALE, Market.DYNAMIC_CONTAINMENT_HIGH],
            SyntheticPriceProvider(), SyntheticPriceProvider(), date(2025, 1, 1),
        )


def test_two_stage_stage2_wholesale_exactly_matches_stage1():
    """The core guarantee of this whole design: stage 2's wholesale
    schedule must be pixel-for-pixel identical to what stage 1
    decided, not re-derived or re-optimized."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day_two_stage(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW],
        SyntheticPriceProvider(), SyntheticPriceProvider(), date(2025, 1, 1),
    )
    assert result.stage2_final.charge_mw == result.stage1_plan.charge_mw
    assert result.stage2_final.discharge_mw == result.stage1_plan.discharge_mw


def test_two_stage_dc_reserve_never_exceeds_power_remaining_after_stage1_wholesale():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day_two_stage(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW],
        SyntheticPriceProvider(), SyntheticPriceProvider(), date(2025, 1, 1),
    )
    for t in range(PERIODS_PER_DAY):
        wholesale_used = result.stage2_final.charge_mw[t] + result.stage2_final.discharge_mw[t]
        dc_reserved = sum(result.stage2_final.reserve_mw[m][t] for m in result.stage2_final.reserve_mw)
        assert wholesale_used + dc_reserved <= battery.power_mw + 1e-6


def test_two_stage_total_revenue_combines_wholesale_and_dc():
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day_two_stage(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW],
        _FlatPriceProvider(), _FlatPriceProvider(), date(2025, 1, 1),
    )
    expected_total = (
        result.stage2_final.revenue_by_market[Market.WHOLESALE]
        + result.stage2_final.revenue_by_market[Market.DYNAMIC_CONTAINMENT_HIGH]
        + result.stage2_final.revenue_by_market[Market.DYNAMIC_CONTAINMENT_LOW]
    )
    assert abs(result.stage2_final.total_revenue - expected_total) < 1e-6
    assert result.stage2_final.total_revenue > 0  # a real, non-trivial answer, not a degenerate zero result


def test_two_stage_stage2_reports_stage2_prices_own_wholesale_price_not_stage1s():
    """A real design point worth proving, not just asserting: stage 2
    reports whatever wholesale price ITS OWN price provider gives,
    even though the physical schedule itself is fixed from stage 1 --
    this is the "wholesale outcome is now a known fact, re-fetch it
    fresh" behaviour, not a copy of stage 1's forecast."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    stage1_prices = _FlatPriceProvider(wholesale_price=50.0)
    stage2_prices = _FlatPriceProvider(wholesale_price=999.0)  # deliberately different from stage 1
    result = optimize_day_two_stage(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH], stage1_prices, stage2_prices, date(2025, 1, 1),
    )
    assert result.stage2_final.wholesale_price == [999.0] * PERIODS_PER_DAY


def test_two_stage_stage1_plan_dc_reserve_is_a_discarded_estimate_not_final():
    """Stage 1's own DC numbers must not silently equal stage 2's final
    ones by coincidence in this test setup -- using genuinely different
    DC prices between stages, proving stage 2 re-decides DC rather than
    inheriting stage 1's estimate."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    stage1_prices = _FlatPriceProvider(dc_high_price=5.0, dc_low_price=5.0)
    stage2_prices = _FlatPriceProvider(dc_high_price=500.0, dc_low_price=500.0)
    result = optimize_day_two_stage(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH], stage1_prices, stage2_prices, date(2025, 1, 1),
    )
    # with DC far more attractive in stage 2, stage 2's DC revenue should exceed stage 1's own DC estimate
    assert (
        result.stage2_final.revenue_by_market[Market.DYNAMIC_CONTAINMENT_HIGH]
        > result.stage1_plan.revenue_by_market[Market.DYNAMIC_CONTAINMENT_HIGH]
    )
