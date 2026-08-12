"""GB market/product definitions.

Each market is a distinct revenue stream with its own settlement
mechanics. MarketSpec + MARKET_REGISTRY is the data-driven answer to
"how do I add a market beyond DC/wholesale/BM": add a registry entry,
don't touch the optimizer's control flow. The optimizer loops over
whatever's in MARKET_REGISTRY generically.

Wholesale is deliberately NOT in MARKET_REGISTRY -- it's not a
capacity commitment with a direction, it's the underlying charge/
discharge decision the optimizer always has available. Everything in
MARKET_REGISTRY is a "reserve" product: capacity held available,
constrained by a specific direction and a delivery duration.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Market(str, Enum):
    WHOLESALE = "wholesale"
    BM_OFFER = "bm_offer"
    BM_BID = "bm_bid"
    # Confirmed via NESO's own DCH/DCL tags that these are genuinely
    # opposite-direction products, not one product under two names.
    # DC-High is triggered by HIGH frequency (system has excess) --
    # the battery absorbs -- charge-direction, needs footroom.
    # DC-Low is triggered by LOW frequency (system is short) -- the
    # battery injects -- discharge-direction, needs headroom.
    # Corrected mid-design: an earlier version of this file had the
    # two directions backwards. See ROADMAP.md Phase A.
    DYNAMIC_CONTAINMENT_HIGH = "dynamic_containment_high"
    DYNAMIC_CONTAINMENT_LOW = "dynamic_containment_low"
    # Not yet in MARKET_REGISTRY -- defined so Scenario configs and
    # historical data can reference them; real future work per
    # ROADMAP.md's backlog (reserve services alongside response).
    # DYNAMIC_REGULATION = "dynamic_regulation"
    # DYNAMIC_MODERATION = "dynamic_moderation"
    # Not a dispatch decision -- Capacity Market is a duration-derated
    # annual availability payment. See docs/adr/0002 and results.py.
    # Never in MARKET_REGISTRY; optimizer.py ignores it.
    # CAPACITY_MARKET = "capacity_market"


@dataclass(frozen=True)
class MarketSpec:
    """What the optimizer needs to know about a reserve-style market,
    as data rather than a special case in the constraint-building
    loop.

    direction: which way the battery must be able to move if this
      commitment is called. "discharge" needs headroom (energy above
      the floor); "charge" needs footroom (space below the ceiling).
    delivery_hours: how long the commitment must be sustainable for,
      once called -- drives how much headroom/footroom a given
      reserve[t] actually requires.
    settlement_unit: "per_mw_h" for an availability-style payment (you
      get paid for holding capacity ready, whether called or not --
      how DC is actually settled). "per_mwh" for an energy-style
      payment (you get paid only for delivered MWh, the accepted-price
      case -- how BM actually works). BM's price here is necessarily
      an expected-value proxy, not a real forecast, since BM can't be
      known day-ahead at all -- see ROADMAP.md Phase B. Currently
      informational/documentary only: v1's revenue formula
      (reserve x dt x price) is the same regardless of this field: for
      an availability market that's a literal reservation fee, for BM
      it's already-probability-weighted expected value. Worth
      revisiting if that stops being an adequate approximation.
    """

    name: Market
    direction: Literal["charge", "discharge"]
    delivery_hours: float
    settlement_unit: Literal["per_mwh", "per_mw_h"]


# Delivery durations sourced from NESO Dynamic Services guidance
# (DC ~15 min). BM's default of one settlement period (0.5h) is a
# simplification -- see ROADMAP.md Phase B for why, and what real
# acceptance-duration data would refine it.
MARKET_REGISTRY: dict[Market, MarketSpec] = {
    Market.DYNAMIC_CONTAINMENT_HIGH: MarketSpec(
        Market.DYNAMIC_CONTAINMENT_HIGH, direction="charge", delivery_hours=0.25, settlement_unit="per_mw_h"
    ),
    Market.DYNAMIC_CONTAINMENT_LOW: MarketSpec(
        Market.DYNAMIC_CONTAINMENT_LOW, direction="discharge", delivery_hours=0.25, settlement_unit="per_mw_h"
    ),
    Market.BM_OFFER: MarketSpec(
        Market.BM_OFFER, direction="discharge", delivery_hours=0.5, settlement_unit="per_mwh"
    ),
    Market.BM_BID: MarketSpec(
        Market.BM_BID, direction="charge", delivery_hours=0.5, settlement_unit="per_mwh"
    ),
}

# Great Britain settlement period length, in hours -- 48 periods/day.
SETTLEMENT_PERIOD_HOURS = 0.5


def market_display_name(market: Market) -> str:
    """Human-readable label for a market -- one place for this,
    rather than the same `.replace("_", " ").title()` (and its "Bm
    Offer" instead of "BM Offer" quirk) duplicated in forms.py,
    views.py, and plotting.py."""
    return market.value.replace("_", " ").title().replace("Bm ", "BM ")
