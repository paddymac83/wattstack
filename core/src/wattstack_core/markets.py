"""GB market/product definitions.

Each market is a distinct revenue stream with its own settlement
mechanics. This module is deliberately the only place that should
need to change when GB market rules change (a new response product,
a changed delivery duration), or when rules for a second market are
added later.
"""
from __future__ import annotations

from enum import Enum


class Market(str, Enum):
    ENERGY = "energy"
    DYNAMIC_CONTAINMENT = "dynamic_containment"
    DYNAMIC_REGULATION = "dynamic_regulation"
    DYNAMIC_MODERATION = "dynamic_moderation"
    # Not yet wired into the daily-dispatch optimizer -- Capacity
    # Market is a duration-derated annual availability payment, not a
    # dispatch decision. See docs/adr/0002 and results.py. Included
    # here so Scenario configs can reference it; optimizer.py ignores
    # it for now.
    CAPACITY_MARKET = "capacity_market"


# Delivery duration a response commitment must be able to sustain if
# called, in hours. Drives the SOC headroom constraint in the
# optimizer. Source: NESO Dynamic Services guidance (DC/DM ~15 min,
# DR ~60 min). DM is approximated at the same duration as DC pending
# confirmation against NESO's current published figures.
RESPONSE_DELIVERY_HOURS: dict[Market, float] = {
    Market.DYNAMIC_CONTAINMENT: 0.25,
    Market.DYNAMIC_MODERATION: 0.25,
    Market.DYNAMIC_REGULATION: 1.0,
}

RESPONSE_MARKETS: tuple[Market, ...] = (
    Market.DYNAMIC_CONTAINMENT,
    Market.DYNAMIC_REGULATION,
    Market.DYNAMIC_MODERATION,
)

# Great Britain settlement period length, in hours -- 48 periods/day.
SETTLEMENT_PERIOD_HOURS = 0.5
