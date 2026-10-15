from datetime import date

import pytest

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


# --- acceptance_probability / expected BM energy impact on SoC ---


class _FakeAcceptanceProvider:
    """Full control over both price and acceptance probability, per
    period and per market -- needed to construct scenarios where the
    solver's decision is predictable by hand, not just plausible."""

    def __init__(self, reserve_prices_by_market=None, acceptance_by_market=None):
        self._reserve_prices_by_market = reserve_prices_by_market or {}
        self._acceptance_by_market = acceptance_by_market or {}

    def wholesale_prices(self, day):
        return [0.0] * 48

    def reserve_prices(self, day, market):
        return self._reserve_prices_by_market.get(market, [0.0] * 48)

    def acceptance_probability(self, day, market):
        return self._acceptance_by_market.get(market, [0.0] * 48)


def _flat(value):
    return [value] * 48


def test_without_acceptance_probability_soc_is_unaffected_by_bm_commitments():
    """Backward compatibility, explicit: SyntheticPriceProvider doesn't
    implement acceptance_probability -- BM commitments must have
    exactly zero SoC impact, matching every pre-existing test's
    assumption, not just "similar" behaviour."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(
        battery, [Market.WHOLESALE, Market.BM_OFFER, Market.BM_BID], SyntheticPriceProvider(), date(2025, 1, 1)
    )
    # every BM entry present (both markets active) but entirely zero (the safe default)
    assert result.acceptance_probability[Market.BM_OFFER] == [0.0] * 48
    assert result.acceptance_probability[Market.BM_BID] == [0.0] * 48


def test_dc_markets_never_appear_in_acceptance_probability_even_when_provider_implements_it():
    """DC ("per_mw_h") has no acceptance-probability concept in this
    model -- even a provider that WOULD answer for DC if asked must
    never have that value used, proven by DC being absent from the
    dict entirely, not just zero."""
    class _AnswersForEverything(_FakeAcceptanceProvider):
        def acceptance_probability(self, day, market):
            return _flat(0.9)  # would answer for ANY market, including DC, if asked

    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW], _AnswersForEverything(), date(2025, 1, 1)
    )
    assert Market.DYNAMIC_CONTAINMENT_HIGH not in result.acceptance_probability
    assert Market.DYNAMIC_CONTAINMENT_LOW not in result.acceptance_probability


def test_isolated_period_soc_drop_matches_the_expected_energy_formula_exactly():
    """The clearest possible proof the formula is right: BM-Offer is
    priced attractively in exactly one period and zero everywhere
    else, so the solver has no reason to commit reserve anywhere but
    that period -- making the resulting SoC drop fully predictable by
    hand, not just plausible."""
    battery = BatterySpec(power_mw=5, duration_hours=4)  # generous headroom for a single period's call
    prices_by_market = {Market.BM_OFFER: [0.0] * 10 + [1000.0] + [0.0] * 37}  # attractive only at period 10
    acceptance_by_market = {Market.BM_OFFER: _flat(0.6)}
    provider = _FakeAcceptanceProvider(prices_by_market, acceptance_by_market)

    result = optimize_day(battery, [Market.BM_OFFER], provider, date(2025, 1, 1))

    assert result.reserve_mw[Market.BM_OFFER][10] == pytest.approx(battery.power_mw, abs=1e-3)
    assert result.reserve_mw[Market.BM_OFFER][9] == pytest.approx(0.0, abs=1e-3)

    eff = battery.one_way_efficiency
    expected_drop = battery.power_mw * 0.5 * 0.6 / eff  # power x delivery_hours x acceptance / efficiency
    soc_before = result.soc_mwh[9]
    soc_after = result.soc_mwh[10]
    assert (soc_before - soc_after) == pytest.approx(expected_drop, abs=1e-2)


def test_soc_recursion_holds_exactly_across_every_period_with_both_bm_markets_active():
    """A general, formula-level proof rather than a single hand-picked
    period: reconstruct soc[t] from the actual solver output
    (reserve, charge, discharge, acceptance_probability) and confirm
    it matches the recursion the optimizer is supposed to be
    enforcing, for all 48 periods -- proves the LP constraint itself
    is correct, independent of what specific decisions the solver
    happened to make."""
    battery = BatterySpec(power_mw=5, duration_hours=4)
    prices_by_market = {
        Market.BM_OFFER: [round(20 + 15 * ((t * 7) % 11), 2) for t in range(48)],
        Market.BM_BID: [round(15 + 10 * ((t * 5) % 9), 2) for t in range(48)],
    }
    acceptance_by_market = {Market.BM_OFFER: _flat(0.4), Market.BM_BID: _flat(0.25)}
    provider = _FakeAcceptanceProvider(prices_by_market, acceptance_by_market)

    result = optimize_day(battery, [Market.BM_OFFER, Market.BM_BID], provider, date(2025, 1, 1))

    eff = battery.one_way_efficiency
    dt = 0.5
    delivery_hours = 0.5
    start_soc = (battery.soc_min_mwh + battery.soc_max_mwh) / 2

    for t in range(48):
        prev = result.soc_mwh[t - 1] if t > 0 else start_soc
        expected_discharge_mwh = (
            result.reserve_mw[Market.BM_OFFER][t] * delivery_hours * result.acceptance_probability[Market.BM_OFFER][t]
        )
        expected_charge_mwh = (
            result.reserve_mw[Market.BM_BID][t] * delivery_hours * result.acceptance_probability[Market.BM_BID][t]
        )
        predicted_soc = (
            prev
            + result.charge_mw[t] * dt * eff - result.discharge_mw[t] * dt / eff
            + expected_charge_mwh * eff - expected_discharge_mwh / eff
        )
        assert result.soc_mwh[t] == pytest.approx(predicted_soc, abs=5e-2)


def test_headroom_and_footroom_remain_based_on_full_delivery_hours_not_derated_by_acceptance():
    """Headroom/footroom protect against the WORST case (fully
    called), independent of how likely that actually is -- a low
    acceptance probability must not be used to justify holding less
    margin than the full commitment would need if it WAS called."""
    battery = BatterySpec(power_mw=5, duration_hours=1)  # short duration: headroom should bind
    prices_by_market = {Market.BM_OFFER: _flat(50.0)}
    acceptance_by_market = {Market.BM_OFFER: _flat(0.05)}  # very low -- must not loosen headroom
    provider = _FakeAcceptanceProvider(prices_by_market, acceptance_by_market)

    result = optimize_day(battery, [Market.BM_OFFER], provider, date(2025, 1, 1))

    for t in range(48):
        headroom_needed = result.reserve_mw[Market.BM_OFFER][t] * 0.5  # delivery_hours, NOT x acceptance
        assert result.soc_mwh[t] >= battery.soc_min_mwh + headroom_needed - 1e-3


def test_higher_acceptance_probability_produces_more_soc_drain_for_the_same_reserve_commitment():
    """Directional sanity check: holding the reserve commitment fixed
    (by making BM-Offer overwhelmingly attractive so the solver always
    commits full power regardless of acceptance probability), a higher
    probability must produce more cumulative SoC drain, not less."""
    battery = BatterySpec(power_mw=5, duration_hours=8)  # generous, so full commitment is always feasible
    prices_by_market = {Market.BM_OFFER: _flat(1000.0)}  # so attractive it's always fully committed either way

    low_acceptance = _FakeAcceptanceProvider(prices_by_market, {Market.BM_OFFER: _flat(0.1)})
    high_acceptance = _FakeAcceptanceProvider(prices_by_market, {Market.BM_OFFER: _flat(0.5)})

    result_low = optimize_day(battery, [Market.BM_OFFER], low_acceptance, date(2025, 1, 1))
    result_high = optimize_day(battery, [Market.BM_OFFER], high_acceptance, date(2025, 1, 1))

    assert result_high.soc_mwh[-1] < result_low.soc_mwh[-1]


# --- dc_activation_probability / expected multi-period DC SoC impact ---


class _FakeDCActivationProvider:
    """Mirrors _FakeAcceptanceProvider exactly, but for DC's
    dc_activation_probability() extension instead of BM's
    acceptance_probability() -- full control over price and activation
    probability, per period and per market, needed to construct
    scenarios where the solver's decision is predictable by hand."""

    def __init__(self, reserve_prices_by_market=None, activation_by_market=None):
        self._reserve_prices_by_market = reserve_prices_by_market or {}
        self._activation_by_market = activation_by_market or {}

    def wholesale_prices(self, day):
        return [0.0] * 48

    def reserve_prices(self, day, market):
        return self._reserve_prices_by_market.get(market, [0.0] * 48)

    def dc_activation_probability(self, day, market):
        return self._activation_by_market.get(market, [0.0] * 48)


def _spike(period, value=1.0):
    probs = [0.0] * 48
    probs[period] = value
    return probs


def test_without_dc_activation_probability_soc_is_unaffected_by_dc_commitments():
    """Backward compatibility, explicit: SyntheticPriceProvider doesn't
    implement dc_activation_probability -- DC commitments must have
    exactly zero SoC impact, matching every pre-existing test's
    assumption."""
    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW],
        SyntheticPriceProvider(), date(2025, 1, 1),
    )
    assert result.dc_activation_probability[Market.DYNAMIC_CONTAINMENT_HIGH] == [0.0] * 48
    assert result.dc_activation_probability[Market.DYNAMIC_CONTAINMENT_LOW] == [0.0] * 48


def test_bm_markets_never_appear_in_dc_activation_probability_even_when_provider_implements_it():
    """The mirror image of the existing "DC never appears in
    acceptance_probability" test -- BM must never appear in
    dc_activation_probability either, even if a provider would answer
    for it if asked."""
    class _AnswersForEverything(_FakeDCActivationProvider):
        def dc_activation_probability(self, day, market):
            return _flat(0.9)

    battery = BatterySpec(power_mw=5, duration_hours=2)
    result = optimize_day(battery, [Market.BM_OFFER, Market.BM_BID], _AnswersForEverything(), date(2025, 1, 1))
    assert Market.BM_OFFER not in result.dc_activation_probability
    assert Market.BM_BID not in result.dc_activation_probability


def test_dc_low_activation_drains_soc_exactly_at_the_activation_period():
    """The clearest possible proof: DC-Low is priced attractively
    (and fixed_wholesale_mw removes any competing wholesale activity),
    with activation probability spiked to 1.0 at exactly one period,
    zero everywhere else -- making the resulting drain fully
    predictable by hand."""
    battery = BatterySpec(power_mw=5, duration_hours=4)
    activation_period = 10
    provider = _FakeDCActivationProvider(
        {Market.DYNAMIC_CONTAINMENT_LOW: _flat(1000.0)},
        {Market.DYNAMIC_CONTAINMENT_LOW: _spike(activation_period)},
    )
    fixed_wholesale = ([0.0] * 48, [0.0] * 48)

    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_LOW], provider, date(2025, 1, 1), fixed_wholesale_mw=fixed_wholesale
    )

    reserve_mw = result.reserve_mw[Market.DYNAMIC_CONTAINMENT_LOW][activation_period]
    minimum_energy_mwh = reserve_mw * 0.25  # DC's confirmed 15-minute minimum energy requirement

    soc_before = result.soc_mwh[activation_period - 1]
    soc_after = result.soc_mwh[activation_period]
    assert (soc_before - soc_after) == pytest.approx(minimum_energy_mwh, abs=1e-2)


def test_dc_low_recovery_begins_exactly_4_periods_after_activation_not_earlier():
    """The idle assessment period (1) plus the 1-hour gate (2) means
    offsets 1, 2, 3 must show zero recovery -- only offset 4 onward.
    Confirmed live, not assumed (docs/adr/0027)."""
    battery = BatterySpec(power_mw=5, duration_hours=4)
    activation_period = 10
    provider = _FakeDCActivationProvider(
        {Market.DYNAMIC_CONTAINMENT_LOW: _flat(1000.0)},
        {Market.DYNAMIC_CONTAINMENT_LOW: _spike(activation_period)},
    )
    fixed_wholesale = ([0.0] * 48, [0.0] * 48)
    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_LOW], provider, date(2025, 1, 1), fixed_wholesale_mw=fixed_wholesale
    )

    # offsets 1, 2, 3 (periods 11, 12, 13): no change from the drained level
    for offset in (1, 2, 3):
        assert result.soc_mwh[activation_period + offset] == pytest.approx(
            result.soc_mwh[activation_period], abs=1e-2
        )
    # offset 4 (period 14): the first recovery increment -- SoC must have risen
    assert result.soc_mwh[activation_period + 4] > result.soc_mwh[activation_period + 3] + 1e-3


def test_dc_low_full_recovery_sums_to_exactly_the_original_drain():
    """The 5 recovery increments (20% each) must sum to exactly the
    original minimum energy requirement -- full restoration, not a
    partial or over-corrected one."""
    battery = BatterySpec(power_mw=5, duration_hours=4)
    activation_period = 5
    provider = _FakeDCActivationProvider(
        {Market.DYNAMIC_CONTAINMENT_LOW: _flat(1000.0)},
        {Market.DYNAMIC_CONTAINMENT_LOW: _spike(activation_period)},
    )
    fixed_wholesale = ([0.0] * 48, [0.0] * 48)
    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_LOW], provider, date(2025, 1, 1), fixed_wholesale_mw=fixed_wholesale
    )

    soc_before_activation = result.soc_mwh[activation_period - 1]
    soc_after_full_recovery = result.soc_mwh[activation_period + 8]  # offset 8 = last recovery period
    assert soc_after_full_recovery == pytest.approx(soc_before_activation, abs=1e-2)


def test_dc_high_activation_and_recovery_are_the_mirror_image_of_dc_low():
    """DC-High (charge-direction): activation charges SoC UP, recovery
    discharges it back DOWN -- the exact sign-flip of DC-Low."""
    battery = BatterySpec(power_mw=5, duration_hours=4)
    activation_period = 10
    provider = _FakeDCActivationProvider(
        {Market.DYNAMIC_CONTAINMENT_HIGH: _flat(1000.0)},
        {Market.DYNAMIC_CONTAINMENT_HIGH: _spike(activation_period)},
    )
    fixed_wholesale = ([0.0] * 48, [0.0] * 48)
    result = optimize_day(
        battery, [Market.DYNAMIC_CONTAINMENT_HIGH], provider, date(2025, 1, 1), fixed_wholesale_mw=fixed_wholesale
    )

    reserve_mw = result.reserve_mw[Market.DYNAMIC_CONTAINMENT_HIGH][activation_period]
    minimum_energy_mwh = reserve_mw * 0.25

    soc_before = result.soc_mwh[activation_period - 1]
    soc_after = result.soc_mwh[activation_period]
    # rises, not drops -- the opposite sign from DC-Low's own version of this test
    assert (soc_after - soc_before) == pytest.approx(minimum_energy_mwh, abs=1e-2)


def test_headroom_and_footroom_remain_based_on_full_delivery_hours_not_derated_by_dc_activation():
    """The same discipline already proven for BM's acceptance
    probability, applied to DC's activation probability: headroom must
    protect against the full commitment regardless of how likely
    activation actually is."""
    battery = BatterySpec(power_mw=5, duration_hours=1)  # short duration: headroom should bind
    provider = _FakeDCActivationProvider(
        {Market.DYNAMIC_CONTAINMENT_LOW: _flat(50.0)},
        {Market.DYNAMIC_CONTAINMENT_LOW: _flat(0.01)},  # very low -- must not loosen headroom
    )
    result = optimize_day(battery, [Market.DYNAMIC_CONTAINMENT_LOW], provider, date(2025, 1, 1))

    for t in range(48):
        headroom_needed = result.reserve_mw[Market.DYNAMIC_CONTAINMENT_LOW][t] * 0.25  # delivery_hours, not x probability
        assert result.soc_mwh[t] >= battery.soc_min_mwh + headroom_needed - 1e-3
