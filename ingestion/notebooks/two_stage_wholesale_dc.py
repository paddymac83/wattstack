from datetime import date, timedelta
import sys; sys.path.insert(0, '../core/src')

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day_two_stage
from wattstack_ingestion.cache import Cache
from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.neso import NesoClient
from wattstack_ingestion.prices import ElexonWholesalePriceProvider, NesoDCPriceProvider, CombinedPriceProvider

# One shared cache + one shared client per source -- avoids re-fetching
# the same data across providers, and across repeated runs of this script.
cache = Cache("wattstack_test_cache.sqlite")
elexon = ElexonClient(cache=cache)
neso = NesoClient(cache=cache)

wholesale = ElexonWholesalePriceProvider(client=elexon, lookback_days=21)
dc = NesoDCPriceProvider(client=neso, lookback_days=90)

# Same provider instance used for both stage 1 and stage 2 -- deliberate,
# not an oversight: neither wholesale nor DC has a stage-specific
# refinement built yet (both are seasonal averages, which wouldn't
# genuinely differ between the 09:50 and 14:00 decision points on the
# same day). Swap in a separate, more current stage2 instance once/if
# that refinement exists -- optimize_day_two_stage() takes stage1_prices
# and stage2_prices as two independent arguments specifically so this
# is a one-line change later, not a redesign.
prices = CombinedPriceProvider(wholesale_provider=wholesale, reserve_providers=[dc])

battery = BatterySpec(power_mw=5, duration_hours=2)
target_day = date.today() + timedelta(days=1)

breakpoint()

result = optimize_day_two_stage(
    battery,
    [Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW],
    prices,  # stage1_prices
    prices,  # stage2_prices
    target_day,
)

print('=== Stage 1 plan (informs the wholesale commitment; its own DC numbers are a discarded estimate) ===')
print('Stage 1 wholesale revenue:', result.stage1_plan.revenue_by_market[Market.WHOLESALE])
print('Stage 1 DC-High revenue (estimate, discarded):', result.stage1_plan.revenue_by_market[Market.DYNAMIC_CONTAINMENT_HIGH])
print('Stage 1 DC-Low revenue (estimate, discarded):', result.stage1_plan.revenue_by_market[Market.DYNAMIC_CONTAINMENT_LOW])

print()
print('=== Stage 2 final (the real answer -- use this, not stage1_plan, for anything downstream) ===')
print('Total revenue:', result.stage2_final.total_revenue)
print('Revenue by market:', result.stage2_final.revenue_by_market)
print('Wholesale price series:', result.stage2_final.wholesale_price)
print('DC-High price series:', result.stage2_final.reserve_price[Market.DYNAMIC_CONTAINMENT_HIGH])
print('DC-Low price series:', result.stage2_final.reserve_price[Market.DYNAMIC_CONTAINMENT_LOW])

print()
print('SoC trajectory:', result.stage2_final.soc_mwh)
print('Charge (MW) by period:', result.stage2_final.charge_mw)
print('Discharge (MW) by period:', result.stage2_final.discharge_mw)
print('Reserve allocation by market:', result.stage2_final.reserve_mw)

print()
print('=== Sanity check: stage 2 wholesale must exactly match stage 1 (both lines should print True) ===')
print(result.stage2_final.charge_mw == result.stage1_plan.charge_mw)
print(result.stage2_final.discharge_mw == result.stage1_plan.discharge_mw)