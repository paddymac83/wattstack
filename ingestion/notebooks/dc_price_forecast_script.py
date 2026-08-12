from datetime import date, timedelta
from wattstack_ingestion.neso import NesoClient
from wattstack_ingestion.prices import NesoDCPriceProvider

class FakeMarket:
    def __init__(self, name): self.name = name

client = NesoClient()
provider = NesoDCPriceProvider(client=client, lookback_days=90)

dc_high = provider.reserve_prices(date.today() + timedelta(days=1), FakeMarket('DYNAMIC_CONTAINMENT_HIGH'))
dc_low = provider.reserve_prices(date.today() + timedelta(days=1), FakeMarket('DYNAMIC_CONTAINMENT_LOW'))
print('DC-High:', dc_high)
print('DC-Low:', dc_low)
print('DC-High non-zero periods:', sum(1 for p in dc_high if p != 0.0), '/ 48')