# 8. MarketSpec registry, and correcting DC-High/DC-Low direction

Date: 2026-08-05

## Status
Accepted -- implemented (ROADMAP.md v1 Phase A)

## Context
Two problems, tackled together because fixing the second properly
required the first to exist.

1. **No principled way to add a market.** `RESPONSE_DELIVERY_HOURS`
   and `RESPONSE_MARKETS` were hand-maintained dicts/tuples, and
   `optimizer.py`'s headroom constraint hardcoded one direction
   (discharge). Adding a charge-direction market meant touching the
   optimizer's constraint-building loop, not just adding a data entry
   -- exactly the "how do I add a market beyond DC/wholesale/BM"
   problem raised when planning v1.

2. **DC-High and DC-Low had the wrong direction.** The original design
   (in the chat, not yet code) had DC-High as discharge-direction and
   DC-Low as charge-direction. Corrected before any code existed:
   DC-High is triggered by HIGH frequency (system has excess) -- the
   battery absorbs, charge-direction. DC-Low is triggered by LOW
   frequency (system is short) -- the battery injects,
   discharge-direction. Confirmed via NESO's own `DCH`/`DCL` tags on
   the Dynamic Containment forecast dataset. Worth recording that this
   was caught and fixed *before* implementation, not after -- the
   value of writing the design down first.

## Decision
- `MarketSpec` (direction, delivery_hours, settlement_unit) +
  `MARKET_REGISTRY: dict[Market, MarketSpec]` in `markets.py`.
  `optimizer.py` loops over whatever's in the registry generically --
  headroom for discharge-direction commitments (energy above the
  floor), footroom for charge-direction commitments (space below the
  ceiling, a genuinely new constraint, symmetric to headroom). Adding
  a market now means adding a registry entry; the optimizer's
  constraint loop doesn't change.
- `Market.WHOLESALE` is deliberately NOT in `MARKET_REGISTRY` -- it's
  the underlying charge/discharge decision the optimizer always has,
  not a capacity commitment with a direction. Everything in
  `MARKET_REGISTRY` is a reserve product.
- BM modeled as two directional entries, `BM_OFFER` (discharge) and
  `BM_BID` (charge), mirroring DC-High/DC-Low -- a BM Unit's accepted
  action can be either direction, so it needs the same two-entry
  treatment, not one.
- `settlement_unit` (`per_mw_h` for DC's availability-style payment,
  `per_mwh` for BM's energy-style payment) is currently documentary
  only -- v1's revenue formula (`reserve x dt x price`) is unchanged
  either way. For DC that's a literal reservation fee; for BM the
  price is necessarily an already-probability-weighted expected value
  (see ROADMAP.md Phase B's LOLP-calibrated proxy), not a real
  forecast. Worth revisiting if that approximation stops being
  adequate.
- Found and fixed along the way, not part of the original plan: three
  places (`plotting.py`, `web/forms.py`, `web/views.py`) independently
  formatted market names for display, each with the same "Bm Offer"
  instead of "BM Offer" bug. Consolidated into one
  `market_display_name()` helper in `markets.py`.

## Consequences
- Every existing market reference across `core` and `web` needed
  updating in the same change (`Market.ENERGY` -> `Market.WHOLESALE`,
  `Market.DYNAMIC_CONTAINMENT` -> `_HIGH`/`_LOW`, plus renamed
  `PriceProvider`/`DispatchResult` fields) -- a real, wide-reaching
  refactor, not additive. Full test suite (core: 26, web: 6) passing
  is what makes this safe to call done rather than "probably fine."
- `test_dc_high_and_dc_low_are_genuinely_opposite_directions` and
  `test_charge_direction_commitments_reserve_soc_footroom` exist
  specifically so the direction correction can't silently regress --
  this is the kind of mistake that's easy to reintroduce without a
  test naming it directly.
- `DYNAMIC_REGULATION`, `DYNAMIC_MODERATION`, and `CAPACITY_MARKET`
  remain defined on `Market` but are not in `MARKET_REGISTRY` -- they
  exist for `Scenario` configs and historical data to reference, but
  contribute nothing to dispatch until they get registry entries
  (ROADMAP.md backlog: reserve services alongside response).
