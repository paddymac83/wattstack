from datetime import date, timedelta
import sys; sys.path.insert(0, '../core/src')

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day
from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.neso import NesoClient
from wattstack_ingestion.prices import (
    ElexonWholesalePriceProvider, NesoDCPriceProvider, ElexonBMPriceProvider, CombinedPriceProvider,
)

wholesale = ElexonWholesalePriceProvider(client=ElexonClient(), lookback_days=21)
dc = NesoDCPriceProvider(client=NesoClient(), lookback_days=90)
bm = ElexonBMPriceProvider(client=ElexonClient(), lookback_days=7)  # 336 requests -- the expensive one

provider = CombinedPriceProvider(wholesale_provider=wholesale, reserve_providers=[dc, bm])

battery = BatterySpec(power_mw=5, duration_hours=2)
target_day = date.today() + timedelta(days=1)

breakpoint()

result = optimize_day(
    battery,
    [Market.WHOLESALE, Market.DYNAMIC_CONTAINMENT_HIGH, Market.DYNAMIC_CONTAINMENT_LOW, Market.BM_OFFER, Market.BM_BID],
    provider,
    target_day,
)
print('Total revenue:', result.total_revenue)
print('Revenue by market:', result.revenue_by_market)
print('Wholesale price series:', result.wholesale_price)
print('BM price series:', result.wholesale_price)
print('DC-High price series:', provider.reserve_prices(target_day, Market.DYNAMIC_CONTAINMENT_HIGH))
print('DC-Low price series:', provider.reserve_prices(target_day, Market.DYNAMIC_CONTAINMENT_LOW))


print('SoC trajectory:', result.soc_mwh)
print('Charge (MW) by period:', result.charge_mw)
print('Discharge (MW) by period:', result.discharge_mw)
print('Reserve allocation by market:', result.reserve_mw)


