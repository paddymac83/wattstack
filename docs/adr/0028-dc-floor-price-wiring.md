# 28. Wiring dc_bid_floor_price into a usable calculation, and retiring the two-stage script

Date: 2026-08-10

## Status
Accepted -- implemented

## Context
Two real gaps surfaced from actually trying to use this project as
built: `dc_bid_floor_price()` and `dc_activation_risk_premium()`
(ADR 0026) existed only as standalone functions -- nothing called them,
so there was no way to reach them from an actual end-to-end script the
way `two_stage_wholesale_dc.py` reached `optimize_day_two_stage()`.
Genuinely undebuggable in the way the user actually works.

Separately, and more fundamentally: the DC-only strategy pivot
(ADR 0026) means no wholesale position is ever held. `optimize_day_two_stage()`
exists specifically to commit a wholesale schedule in stage 1 and fix
it for stage 2 -- the wrong tool entirely once there's no wholesale
commitment to make. The two-stage script wasn't just incomplete, it
was solving a problem the current strategy doesn't have.

## Decision
- `dc_bid_floor_prices_by_efa_block()` (new, `analysis.py`): the
  actual wiring `dc_bid_floor_price()` was missing -- given a full
  48-period wholesale forecast, groups periods into their EFA block
  via `efa_block_number_for_hour()` (the same confirmed mapping used
  everywhere else in this project, not reimplemented), and calls
  `dc_bid_floor_price()` once per block. Proven by test that each
  block's result is identical to calling `dc_bid_floor_price()`
  directly with that block's own prices -- genuine wiring, not a
  parallel calculation that could silently drift from the
  already-tested formula.
- A real, stated simplification: `wholesale_prices_during_recovery_window`
  is passed the same block's own prices as
  `wholesale_prices_during_efa_block`, not a window that could
  genuinely extend into the next block for an activation near a
  block's end. Reasonable given the confirmed 8-period recovery
  window and an EFA block's own 8-period length are the same order of
  magnitude -- not claimed to be precise.
- `dc_floor_price_calculator.py` (new script, replacing
  `two_stage_wholesale_dc.py` entirely, not alongside it): fetches a
  pre-09:50 wholesale forecast, computes all 6 EFA blocks' floor
  prices, prints them. At the time of this ADR, no `core` import at
  all -- a direct calculation, not a dispatch optimization, so the LP
  optimizer wasn't involved. **Superseded by `docs/adr/0029`**, written
  the same day: the script was extended in place with a Step 2 that
  does use `core`'s `optimize_day()` for DC-only dispatch planning,
  once `dc_activation_probability()` existed to make that step
  meaningful. `CONTRACTED_MW`, `DEGRADATION_COST_PER_MWH`, and
  `ACTIVATION_PROBABILITY` are named, commented constants at the top --
  real, unresolved inputs the user supplies, not values this code has
  derived.
- The same floor price is used for both DC-High and DC-Low in the
  script by default -- the formula itself doesn't depend on direction,
  though nothing prevents calling it twice with different
  `ACTIVATION_PROBABILITY` values per direction if that distinction
  matters. Stated directly in the script's own output, not left
  implicit.

## Consequences
- This closes the actual gap the user found: `dc_bid_floor_price()`
  and `dc_activation_risk_premium()` are now reachable, and
  debuggable, from a real script with a real `breakpoint()`, not
  orphaned functions with no caller.
- `two_stage_wholesale_dc.py` is retired, not kept alongside this --
  it solves a problem (active wholesale commitment) the current
  strategy doesn't have. If the strategy changes again to hold a
  wholesale position, `optimize_day_two_stage()` itself is unchanged
  and still correct; only the *script* using it was strategy-specific
  and needed replacing.
- `ACTIVATION_PROBABILITY` remains the same real, unresolved number
  named as a gap in ADR 0024, 0026, and 0027 -- this ADR doesn't
  change that. It's now at least visible and adjustable in one place
  in a real script, rather than buried as a function parameter with
  no caller.
