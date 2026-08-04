"""Price data for the optimizer.

PriceProvider is the seam between the optimizer/backtest engine and
wherever price data actually comes from. SyntheticPriceProvider is a
deterministic stand-in for demos and tests -- NOT real market data.
Real implementations (ElexonPriceProvider, NesoPriceProvider) are
ROADMAP.md Phase B, reusing the ingestion pattern already proven in
`ingestion/`.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Protocol

from wattstack_core.markets import Market

PERIODS_PER_DAY = 48


class PriceProvider(Protocol):
    def wholesale_prices(self, day: date) -> list[float]:
        """48 half-hourly GBP/MWh prices for the given day."""
        ...

    def reserve_prices(self, day: date, market: Market) -> list[float]:
        """48 half-hourly prices for a MARKET_REGISTRY market -- unit
        depends on that market's settlement_unit (see markets.py)."""
        ...


class SyntheticPriceProvider:
    """Deterministic, plausibly-shaped synthetic GB prices.

    Not real market data -- useful for demos, tests, and exercising
    the optimizer before real ingestion is wired up. The shape is a
    stylised day-ahead curve (cheap overnight, evening peak) with a
    small amount of day-to-day variation seeded from the date.
    """

    def wholesale_prices(self, day: date) -> list[float]:
        seed = day.toordinal()
        base = 70.0 + 10.0 * math.sin(seed)
        prices = []
        for t in range(PERIODS_PER_DAY):
            hour = t / 2
            # Stylised diurnal shape: overnight trough, morning and
            # evening peaks, midday solar dip.
            shape = (
                -25 * math.exp(-((hour - 3) ** 2) / 8)
                + 15 * math.exp(-((hour - 8) ** 2) / 4)
                - 15 * math.exp(-((hour - 13) ** 2) / 6)
                + 35 * math.exp(-((hour - 18) ** 2) / 4)
            )
            noise = 4 * math.sin(seed + t)
            prices.append(round(max(base + shape + noise, -20.0), 2))
        return prices

    def reserve_prices(self, day: date, market: Market) -> list[float]:
        seed = day.toordinal() + (hash(market.value) % 97)
        base_by_market = {
            Market.DYNAMIC_CONTAINMENT_HIGH.value: 12.0,
            Market.DYNAMIC_CONTAINMENT_LOW.value: 11.0,
            Market.BM_OFFER.value: 20.0,
            Market.BM_BID.value: 8.0,
        }
        b = base_by_market.get(market.value, 5.0)
        return [round(max(b + 2 * math.sin(seed + t / 3), 0.0), 2) for t in range(PERIODS_PER_DAY)]
