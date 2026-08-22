from datetime import date, timedelta
import sys; sys.path.insert(0, 'core/src')

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day
from wattstack_ingestion.analysis import dc_bid_floor_prices_by_efa_block, efa_block_label_for_index
from wattstack_ingestion.cache import Cache
from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.neso import NesoClient
from wattstack_ingestion.prices import ElexonWholesalePriceProvider, NesoDCPriceProvider

# No optimize_day_two_stage() here, deliberately -- that function
# exists for a strategy that holds an active wholesale position, which
# this one doesn't. Step 1 below is a direct calculation (wholesale
# forecast -> floor price per EFA block), not a dispatch optimization,
# so the core LP optimizer isn't involved there at all. Step 2 does
# use the optimizer, but DC-only -- wholesale is fixed at exactly
# zero, never a free decision.

cache = Cache("wattstack_dc_floor_price_cache.sqlite")
elexon = ElexonClient(cache=cache)
neso = NesoClient(cache=cache)
wholesale = ElexonWholesalePriceProvider(client=elexon, lookback_days=21)

# Real, unresolved parameters -- stated plainly, not calibrated.
# Same treatment as everywhere else in this project: tunable inputs
# you supply, not numbers this code has independently derived.
CONTRACTED_MW = 5.0              # how much capacity you're actually offering to DC
DEGRADATION_COST_PER_MWH = 5.0   # your own estimate of battery wear cost, GBP/MWh
ACTIVATION_PROBABILITY = 0.05    # unresolved -- see docs/adr/0024's "future work" on
                                  # calibrating this from largest-loss data (docs/adr/0016);
                                  # not derived here, just a number you supply. The same
                                  # value is used for both the floor price below and the
                                  # NesoDCPriceProvider driving Step 2's SoC modelling, so
                                  # the pricing and dispatch stay consistent with each other.

# NesoDCPriceProvider.activation_probability defaults to 0.02 -- overridden
# here to match ACTIVATION_PROBABILITY above, rather than silently using two
# different, disconnected numbers for the same underlying assumption.
dc = NesoDCPriceProvider(client=neso, lookback_days=90, activation_probability=ACTIVATION_PROBABILITY)

battery = BatterySpec(power_mw=CONTRACTED_MW, duration_hours=2)
target_day = date.today() + timedelta(days=1)


class _NoWholesaleActivity:
    """A tiny adapter, not a real price provider -- optimize_day()
    still fetches wholesale_prices() for revenue reporting whenever
    fixed_wholesale_mw is given, even though the reported wholesale
    revenue is exactly zero regardless of price (a zero schedule times
    any price is zero). NesoDCPriceProvider only implements
    reserve_prices() and dc_activation_probability(), so this fills
    the gap with an always-zero wholesale_prices() and delegates the
    rest straight through, rather than either crashing on a missing
    method or building a real wholesale provider this strategy never
    uses."""

    def __init__(self, dc_provider):
        self._dc = dc_provider

    def wholesale_prices(self, day):
        return [0.0] * 48

    def reserve_prices(self, day, market):
        return self._dc.reserve_prices(day, market)

    def dc_activation_probability(self, day, market):
        return self._dc.dc_activation_probability(day, market)


# --- Step 1 (before 09:50): the floor price per EFA block, to inform
# what to bid into DC by 14:00. Uses only pre-09:50 wholesale
# information -- nothing here depends on the wholesale auction having
# happened.
wholesale_prices = wholesale.wholesale_prices(target_day)

floor_prices = dc_bid_floor_prices_by_efa_block(
    wholesale_prices=wholesale_prices,
    contracted_mw=CONTRACTED_MW,
    degradation_cost_per_mwh=DEGRADATION_COST_PER_MWH,
    activation_probability=ACTIVATION_PROBABILITY,
)


print(f"=== DC bid floor prices for {target_day} ({CONTRACTED_MW}MW contracted) ===")
print("Same floor price used for both DC-High and DC-Low here -- the formula itself doesn't")
print("depend on direction; pass different activation_probability values per direction if you")
print("want them to diverge (e.g. two separate calls with different ACTIVATION_PROBABILITY).")
print()
for block in range(1, 7):
    print(f"EFA block {block} ({efa_block_label_for_index(block)}): £{floor_prices[block]:.2f}/MW/h")

# --- Step 2 (after 14:00, once your bids are in and the auction has
# cleared): dispatch planning for whatever DC capacity actually got
# accepted. Wholesale is fixed at exactly zero rather than just
# omitted from `markets` -- omitting it alone would still leave
# charge/discharge as free LP variables with no revenue incentive, but
# the solver COULD still use them if ever needed for feasibility (e.g.
# to keep SoC within bounds against the DC activation/recovery
# mechanism below) -- silently reintroducing a wholesale position this
# strategy explicitly rules out. Fixing to zero makes "never trades
# wholesale" a guarantee, not an incentive-based assumption.
no_wholesale_activity = ([0.0] * 48, [0.0] * 48)
result = optimize_day(
    battery,
    [Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW],
    _NoWholesaleActivity(dc),
    target_day,
    fixed_wholesale_mw=no_wholesale_activity,
)

print()
print("=== DC-only dispatch plan (no wholesale, matching this strategy) ===")
print('Total revenue:', result.total_revenue)
print('Revenue by market:', result.revenue_by_market)
print('DC-High price series (real, post-clearing):', result.reserve_price[Market.DYNAMIC_CONTAINMENT_HIGH])
print('DC-Low price series (real, post-clearing):', result.reserve_price[Market.DYNAMIC_CONTAINMENT_LOW])
print('DC-High activation probability used:', result.dc_activation_probability[Market.DYNAMIC_CONTAINMENT_HIGH][0])
print('DC-Low activation probability used:', result.dc_activation_probability[Market.DYNAMIC_CONTAINMENT_LOW][0])

print()
print('SoC trajectory:', result.soc_mwh)
print('Reserve allocation by market:', result.reserve_mw)

print()
print('=== Sanity check: wholesale fixed to exactly zero throughout, not just unincentivized ===')
print('Charge (MW) by period -- every value must be 0.0:', result.charge_mw)
print('Discharge (MW) by period -- every value must be 0.0:', result.discharge_mw)
