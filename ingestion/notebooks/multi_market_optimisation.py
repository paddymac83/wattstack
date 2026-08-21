from datetime import date, timedelta
import sys; sys.path.insert(0, '../core/src')

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day
from wattstack_ingestion.cache import Cache
from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.neso import NesoClient
from wattstack_ingestion.prices import (
    ElexonWholesalePriceProvider, NesoDCPriceProvider, ElexonImbalancePriceProvider, CombinedPriceProvider,
)

# One shared cache + one shared client per source -- avoids re-fetching
# the same data across providers, and across repeated runs of this script.
cache = Cache("wattstack_test_cache.sqlite")
elexon = ElexonClient(cache=cache)
neso = NesoClient(cache=cache)

wholesale = ElexonWholesalePriceProvider(client=elexon, lookback_days=21)
dc = NesoDCPriceProvider(client=neso, lookback_days=90)
bm = ElexonImbalancePriceProvider(client=elexon, lookback_days=60)  # the validated model, ~120 requests, cached after first run

provider = CombinedPriceProvider(wholesale_provider=wholesale, reserve_providers=[dc, bm])

battery = BatterySpec(power_mw=5, duration_hours=2)
target_day = date.today() + timedelta(days=1)

breakpoint()

result = optimize_day(
    battery,
    [Market.WHOLESALE, Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW],
    provider,
    target_day,
)
print('Total revenue:', result.total_revenue)
print('Revenue by market:', result.revenue_by_market)
print('Wholesale price series:', result.wholesale_price)
print('DC-High price series:', result.reserve_price[Market.DYNAMIC_CONTAINMENT_HIGH])
print('DC-Low price series:', result.reserve_price[Market.DYNAMIC_CONTAINMENT_LOW])
print('BM-Offer price series:', result.reserve_price[Market.BM_OFFER])
print('BM-Bid price series:', result.reserve_price[Market.BM_BID])

print('SoC trajectory:', result.soc_mwh)
print('Charge (MW) by period:', result.charge_mw)
print('Discharge (MW) by period:', result.discharge_mw)
breakpoint()
print('Reserve allocation by market:', result.reserve_mw)