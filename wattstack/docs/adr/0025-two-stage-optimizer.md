# 25. Two-stage optimizer: wholesale first, DC second, matching the real gate closure sequence

Date: 2026-08-10

## Status
Accepted -- implemented

## Context
Following the strategy discussion on day-ahead timing: wholesale gate
closure 09:50, DC/DM/DR/BR/QR gate closure 14:00, both confirmed
directly from NESO's own current guidance (docs/adr/0024). Since DC's
deadline is genuinely *after* wholesale's, and the wholesale auction
result is known well before 14:00, the right architecture is a real
two-stage decision -- wholesale committed first under a probabilistic
view of DC's value, DC committed second with the wholesale outcome
already known -- not a single LP simultaneously guessing at both, and
not a strict either-or fallback relationship between the two.

BM was explicitly descoped from this work -- the user's own decision,
treating BM and intraday wholesale as a fallback layer after DC and
day-ahead wholesale decisions, not part of the primary optimization.

## Decision
- `optimize_day()` gains an optional `fixed_wholesale_mw: tuple[list[float], list[float]] | None`
  parameter -- a wholesale schedule already decided elsewhere, not a
  free decision for this call. `Market.WHOLESALE` must not also appear
  in `markets` when this is given; checked and rejected explicitly
  (`ValueError`), not silently resolved one way. Validated against
  length (must be `PERIODS_PER_DAY`) and physical bounds (`[0, power_mw]`
  per period) before any LP construction -- fail fast, clear errors,
  not a confusing downstream infeasibility.
- The existing LP structure required no duplication to support this:
  PuLP accepts plain floats interchangeably with `LpVariable`s in
  constraint expressions, so `charge[t]`/`discharge[t]` become the
  given constants rather than free variables, and every downstream
  constraint (power budget, SoC recursion, headroom/footroom) and the
  objective (`pulp.value()`, not `.varValue`, already handles both
  transparently) work unchanged. One new code path, not two parallel
  implementations to keep in sync.
- Wholesale price is fetched, and wholesale revenue computed and
  reported, whenever a wholesale position exists at all -- free *or*
  fixed (`wholesale_known = wholesale_active or wholesale_fixed`), not
  only when it's a free decision. A fixed schedule's revenue is a
  constant given known price and known schedule; including it in the
  objective is mathematically harmless (a constant doesn't move where
  the optimum for the remaining free variables sits) and keeps the
  code path uniform rather than special-cased.
- `optimize_day_two_stage()` (new): stage 1 runs the existing joint
  `optimize_day()` across wholesale plus the given `dc_markets`, using
  `stage1_prices` for both -- necessary because blindly optimizing
  wholesale alone, ignorant of DC's likely value, could commit
  capacity that would have been worth more held back. Only stage 1's
  wholesale schedule is kept; its own DC numbers are a planning
  estimate, made before DC's real auction has cleared, and discarded.
  Stage 2 re-runs `optimize_day()` with `dc_markets` only (no
  wholesale in the free decision), `fixed_wholesale_mw` set to stage
  1's wholesale schedule, and `stage2_prices` -- which may differ from
  `stage1_prices` and is the caller's choice, not assumed identical.
- `TwoStageResult` (new, `results.py`): `stage1_plan` and
  `stage2_final`, deliberately not generic names -- makes misusing
  stage 1's discarded DC estimate as if it were the real answer
  harder, not just documented against.

## Consequences
- **A real infeasibility bug found and fixed during testing, not a
  hypothetical edge case:** feeding stage 1's own `charge_mw`/
  `discharge_mw` (rounded to 4 decimal places for reporting) back into
  stage 2 as `fixed_wholesale_mw` accumulated a SoC drift of a few
  thousandths of an MWh across 48 periods -- physically meaningless
  for any real battery, but enough to violate zero-tolerance hard LP
  bounds and report the whole problem infeasible. Diagnosed by hand-
  recomputing the SoC trajectory from stage 1's actual rounded output,
  not guessed at. Fixed with a small, deliberately targeted tolerance
  (`1e-3` MWh) applied only when `wholesale_fixed` -- the general,
  free-variable path is untouched and needs no tolerance, since a
  freely solved SoC variable is never handed a pre-rounded input to
  begin with; confirmed by every existing single-stage test passing
  unchanged both before and after this fix.
- Proven directly by test, not just described: stage 2's wholesale
  schedule is pixel-identical to stage 1's
  (`test_two_stage_stage2_wholesale_exactly_matches_stage1`); DC
  reserve plus the fixed wholesale schedule never exceeds rated power
  in any period; stage 2 reports its *own* price provider's wholesale
  price for revenue, not stage 1's forecast, even though the physical
  schedule itself is fixed
  (`test_two_stage_stage2_reports_stage2_prices_own_wholesale_price_not_stage1s`);
  and stage 2 genuinely re-decides DC rather than inheriting stage 1's
  estimate, shown by deliberately using very different DC prices
  between stages and confirming stage 2's DC revenue tracks its own,
  not stage 1's.
- What's next, not done here: the actual updated end-to-end test
  script exercising this against real `PriceProvider`s (wholesale +
  DC only, no BM, matching the user's own descoping decision).
