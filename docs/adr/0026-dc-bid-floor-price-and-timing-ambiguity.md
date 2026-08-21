# 26. DC bid floor price, and a timing ambiguity surfaced while designing SoC modeling

Date: 2026-08-10

## Status
Accepted -- `dc_bid_floor_price()` implemented. The SoC modeling
extension discussed below is deliberately NOT implemented in this ADR
-- see Consequences.

## Context
Following the strategy pivot to DC-only bidding (wholesale as a pure
opportunity-cost proxy, priced using pre-09:50 information, no active
wholesale or BM decisions), a real DC bid floor needed the wholesale
opportunity-cost component `dc_activation_risk_premium()` (ADR 0024)
didn't cover on its own -- that function only prices the *additional*
opportunity cost specific to the post-activation recovery window, not
the baseline cost of committing capacity to DC for the whole EFA block
in the first place, activation or not.

While designing the requested DC SoC optimization (properly modelling
the 20%-per-SP recovery, 5%-per-minute ramp, and 1-hour gate as *state*
in the LP, not just a pricing input), re-reading NESO's own worked
example precisely surfaced a genuine ambiguity in ADR 0024's own
timing claim, worth recording honestly rather than quietly
propagating into new code.

NESO's example: an event occurs in SP24; "at the end of SP24/start of
SP25 the stored energy is now 2.5MWh"; a baseline "should be created
and submitted before the end of SP25 so it can take effect from SP28
... because there is a 1 hour gate." ADR 0024 read this as a 2-period
gate (SP26-27) between submission and effect, giving `DC_RECOVERY_GATE_PERIODS=2`
and a 7-period total window (2 gate + 5 recovery). Re-examined here:
SP25 itself -- the period in which assessment and submission happen --
is *also* a period with no recovery, and isn't clearly the same thing
as the "1 hour gate" the document names. Two readings remain genuinely
open: (a) the gate is measured from the *latest possible* submission
moment (end of SP25), making SP25 a real but separate delay on top of
the 2-period gate -- an 8-period total window, not 7; or (b) the
document's own framing ("it cannot take effect earlier *because of*
the 1 hour gate") is the complete explanation and SP25 is just the
window in which submission is allowed, not an additional delay --
supporting the original 7-period reading. Both are defensible readings
of the same prose. Not resolved here -- flagged honestly rather than
picking one with more confidence than is warranted.

## Decision
- `dc_bid_floor_price()`, `analysis.py` -- combines a new baseline
  opportunity-cost term (wholesale price spread across the EFA block,
  divided by EFA block hours -- the same spread-based methodology
  already used in `dc_activation_risk_premium()`, for consistency
  between the two rather than a different technique) with the
  existing `dc_activation_risk_premium()` unchanged. An explicit,
  additive combination, not a rewrite of either component.
- **The SoC modeling extension requested alongside this is
  deliberately NOT built in this ADR.** Given the timing ambiguity
  above is genuinely unresolved, and given this would be new,
  multi-period LP logic in `core` (spreading an activation's expected
  SoC impact across several future periods, not the same-period
  BM-style expected value already built) -- building it on a timing
  assumption that might be off by one settlement period risks
  shipping something confidently wrong rather than honestly
  incomplete. The existing `DC_RECOVERY_GATE_PERIODS=2`/
  `DC_RECOVERY_PERIODS_FROM_EMPTY=5`/`DC_ACTIVATION_RECOVERY_WINDOW_PERIODS=7`
  constants are left unchanged, not "corrected" toward either reading,
  since neither is confirmed over the other.

## Consequences
- `dc_bid_floor_price()` is ready to use now -- it doesn't depend on
  the ambiguous timing question, only on the already-confirmed 15-min
  minimum energy requirement and the already-tested activation
  premium.
- The requested "DC-only should be easier" SoC modeling remains
  genuinely useful, real future work -- the design direction (an
  expected-value model spreading a possible activation's SoC impact
  across the recovery window, linear and LP-compatible, the same
  shape as BM's existing expected-energy mechanism but spread across
  multiple future periods instead of the same one) is sound regardless
  of whether the window is 7 or 8 periods -- only the exact spread
  needs the ambiguity resolved first.
- Removing wholesale and BM as active decisions genuinely does
  simplify the *interaction* between markets in the SoC recursion (one
  fewer source of competing SoC pressure) -- but does not remove the
  fundamental difficulty that activation timing is stochastic and
  unknown at day-ahead planning time. "Easier" was directionally
  right; "easy" would have been an overclaim.
