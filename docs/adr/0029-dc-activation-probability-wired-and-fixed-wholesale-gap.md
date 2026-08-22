# 29. Closing the loop: dc_activation_probability wired to a real provider, and a fixed_wholesale_mw gap

Date: 2026-08-10

## Status
Accepted -- implemented

## Context
`docs/adr/0027` built the DC multi-period SoC mechanism and the
optional `dc_activation_probability()` `PriceProvider` extension, but
no real provider implemented it -- pointed out directly: *"I cannot
debug them as part of a script that I would usually write that ran an
optimisation end to end."* A genuinely valid critique: `dc_bid_floor_price()`,
`dc_activation_risk_premium()`, and `dc_activation_probability` all
existed as real, tested pieces with no path connecting them to an
actual runnable script.

Separately, the user's real strategy was restated precisely: no active
wholesale position, ever -- wholesale exists purely as the opportunity-
cost input to the DC floor price, using only pre-09:50 information.
`optimize_day_two_stage()` is the wrong tool for this: it exists
specifically for when wholesale *is* an active decision (even jointly,
in stage 1). This strategy needs a genuinely different, simpler shape:
compute the DC floor price per EFA block before 09:50, then run a
DC-only dispatch plan with wholesale fixed at exactly zero, not merely
absent from `markets`.

One thing worth naming honestly: a search for `dc_bid_floor_price_by_efa_block`
(singular "price") returned nothing, and was initially reported as a
missing function. It already existed, correctly implemented and
tested, as `dc_bid_floor_prices_by_efa_block` (plural) -- a genuine
search error, not a genuine gap. Caught by re-checking with the
correct name and running the existing tests directly rather than
trusting the first (wrong) search result.

A second, real gap surfaced while building the DC-only script:
`fixed_wholesale_mw` triggers a `wholesale_prices()` fetch for revenue
reporting whenever it's given, *regardless of whether the schedule is
all zero* -- correct in general (a fixed schedule's revenue is a real
number that needs a real price to compute), but it means any provider
used with `fixed_wholesale_mw` must implement `wholesale_prices()`,
even a DC-only provider that has no wholesale concept at all and never
will. `NesoDCPriceProvider` doesn't implement it and correctly
shouldn't -- it has no business knowing about wholesale.

## Decision
- `NesoDCPriceProvider` gains `dc_activation_probability(day, market)`
  and a new `activation_probability` constructor parameter (default
  `0.02`) -- a single flat constant across all periods and both DC
  markets, the simplest possible starting point, same stated-parameter
  honesty as `ElexonBMPriceProvider.acceptance_derating` and
  `ElexonImbalancePriceProvider`'s own derating factor. Still
  validates the `market` argument the same way `reserve_prices()`
  does, so a caller asking about a market this provider doesn't cover
  gets the same clear error, not a silently wrong answer.
- `dc_floor_price_calculator.py` already existed (Step 1 only --
  `dc_bid_floor_prices_by_efa_block()`, no optimizer involved), found
  while about to build a new, separate script that would have
  duplicated it. Extended in place rather than left alongside a
  redundant new file: Step 2 added, a DC-only `optimize_day()` call
  with `fixed_wholesale_mw=([0.0]*48, [0.0]*48)` explicitly -- not
  just `Market.WHOLESALE` omitted from `markets`, since omitting it
  alone leaves `charge`/`discharge` as free LP variables with no
  revenue incentive, but the solver *could* still use them if ever
  needed for feasibility (e.g. to keep SoC within bounds against the
  DC activation/recovery mechanism), silently reintroducing a
  wholesale position this strategy explicitly rules out. Fixing to
  exactly zero makes "never trades wholesale" a guarantee, not an
  incentive-based assumption that happens to hold under today's
  parameters. `ACTIVATION_PROBABILITY` is now a single constant feeding
  both the floor price calculation and `NesoDCPriceProvider` (via its
  `activation_probability` constructor argument), so pricing and
  dispatch use the same assumption rather than two silently
  disconnected numbers.
- `_NoWholesaleActivity` (in the script, not the library): a minimal
  adapter closing the `fixed_wholesale_mw` + partial-provider gap --
  an always-zero `wholesale_prices()` (the reported revenue is exactly
  zero regardless of the value returned, since a zero schedule times
  any price is zero) delegating `reserve_prices()` and
  `dc_activation_probability()` straight through to the real DC
  provider. Kept local to the script rather than added to `prices.py`
  -- this is a thin, single-purpose composition, not a reusable
  library concept the way `CombinedPriceProvider` is.

## Consequences
- Validated end-to-end with mocked network calls, not just unit
  tests in isolation: `dc_bid_floor_prices_by_efa_block()` produces
  real per-block numbers from a real (mocked) wholesale fetch,
  `dc_activation_probability` is confirmed genuinely readable from the
  `DispatchResult` the optimizer returns (not just present in the
  provider), and charge/discharge are confirmed exactly zero
  throughout, not merely unincentivized.
- The `fixed_wholesale_mw`-requires-`wholesale_prices()` interaction is
  a real, generalisable gap worth remembering for any future DC-only
  or BM-only script: any provider paired with `fixed_wholesale_mw`
  needs at minimum a trivial `wholesale_prices()` implementation, even
  when wholesale is conceptually irrelevant to that provider's actual
  job.
- The search-typo incident (singular vs plural function name) is worth
  naming as a standing lesson, not just corrected quietly: a "function
  X doesn't exist" conclusion from one failed search is not confirmed
  until re-checked with a second method (a different search term,
  or -- as done here -- actually running the tests that would prove
  or disprove it). The same lesson recurred at the script level: a
  new `dc_only_floor_price.py` was written before checking whether
  something equivalent already existed -- `dc_floor_price_calculator.py`
  did, cleanly, with better parameter documentation. Deleted the
  duplicate and extended the original in place, once the check that
  should have happened first, happened.
