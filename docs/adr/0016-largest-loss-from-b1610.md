# 16. Largest secured loss, reconstructed from B1610 -- no dataset exists for it directly

Date: 2026-08-08

## Status
Accepted

## Context
Before treating inertia as the sole driver of DC requirement in the
trading app, the remaining hypothesis needed checking: does
requirement also move with the size of the largest credible loss
NESO must secure against, independent of inertia's own effect?

Searched specifically for a "largest loss" dataset before building
anything -- none exists as a downloadable, structured NESO resource.
Confirmed instead, directly from NESO's own "Frequency Risk and
Control Report": *"Current policy focuses on securing BMU-only events
with their consequential RoCoF loss"* and *"As our largest loss size
increases, with sites such as Hinkley-C power station connecting to
the network in the future, we will see a significant increase in DC
requirements to cover a larger individual loss."* NESO states the
relationship directly -- but the underlying number is a derived
operational concept, not a published time series.

A peer-reviewed paper's own data-availability section pointed at the
real path: *"Per-BMU generation data (B1610) and interconnector flow
data are available through the Elexon BMRS API."* Confirmed
independently via Elexon's own API documentation: `B1610` (Actual
Generation Output Per Generation Unit) is accessed through
`/datasets/B1610` -- a third distinct endpoint family in this
project, alongside `/balancing/...` (opinionated) and `/forecast/...`
(also opinionated). Real, sourced operational detail: B1610 data has
a 5-working-day settlement lag -- irrelevant here (historical
correlation, not a live decision) but a real constraint worth naming.

Interconnector and SIZB identification needed no new tooling --
`filter_bmus_by_id_pattern()`, already built and tested for battery
identification, applies directly. Interconnector direction (import vs
export) is inferred from B1610's value sign, matching the general
generation-positive/demand-negative convention used elsewhere in
Elexon data -- **not independently confirmed for interconnectors
specifically**, stated plainly in code, not glossed over.

## Decision
- `ElexonClient.actual_generation_per_bmu()` / `verify_actual_generation_schema()`,
  built on `/datasets/B1610`, `params=` throughout (ADR 0011's rule
  holds for every new method, not just the ones already fixed).
- `direction_from_sign()` and `largest_value_by_group()`, both in
  `analysis.py`, both deliberately generic (not B1610-specific) --
  the same shape of primitive as `spread_by_bin()`: reusable for
  whatever the next "largest of a filtered group, per period"
  question turns out to be.
- `largest_value_by_group()` does NOT take an absolute value itself
  -- the caller signs/filters first. This keeps the function honest
  about what it does (max of given values) rather than silently
  encoding an assumption about what "loss size" means for every
  possible caller.
- Section 3 of `notebooks/dc_requirements_by_efa_block.py`: SIZB and
  interconnectors identified via ID pattern (shown, not trusted
  blindly, same discipline as the battery-identification work),
  largest import/export exposure computed per day, joined onto the
  existing DC-Low/DC-High + inertia tables.
- **The independence check is a median-tercile stratification, not a
  regression model.** Each comparison chart is built twice: once
  unconditional, once filtered to the middle third of the inertia
  range (`quantile(0.33)` to `quantile(0.67)`). If the loss
  relationship survives inside that narrow inertia band, that's real
  evidence of an independent effect; if it only appears in the full,
  unstratified data, inertia was likely the actual driver. Simpler
  and more interpretable than a regression, consistent with this
  project's standing preference (ADR 0012's ML discussion) for
  methods whose reasoning stays inspectable.

## Consequences
- **Confirmed live, same day, a real mistake corrected rather than a
  detail refined:** `actual_generation_per_bmu()`'s first version used
  `from`/`to` query parameters, sourced from a BSC Insight article and
  a third-party wrapper's convenience function
  (`ElexonDataPortal.get_B1610(start_date, end_date)`) rather than
  Elexon's own API documentation page. The real endpoint takes
  `settlementDate` + `settlementPeriod` -- confirmed directly against
  the documentation page and a real, working request URL. This is the
  exact mistake ADR 0011 named as a standing risk: trusting a
  wrapper's own parameter names as if they were the underlying REST
  API's, rather than checking the API directly. Fixed by rewriting the
  method around the confirmed shape, adding
  `actual_generation_per_bmu_for_day()` (the real cost consequence --
  48 requests per day, not one bulk call), and pinning the exact
  confirmed URL directly in a regression test
  (`test_actual_generation_per_bmu_matches_the_real_confirmed_url_exactly`)
  so this specific mistake can't quietly return.
- The notebook's B1610 fetch cell was rewritten around the same
  correction: an explicit `b1610_days_to_fetch` slider with a live
  cost readout (matching the acceptances-heavy notebooks elsewhere in
  this project) replaces what had been an unbounded fetch across
  whatever date range `labelled_df` happened to cover -- that design
  was safe under the wrong from/to assumption and became a real risk
  of an unbounded request count once the true per-period cost was
  known.
- The `b1610_date_field` dropdown was removed entirely, not just
  relabelled -- days are now attached from the fetch loop itself
  (`_day`, the day actually requested), the same robust pattern
  already used for the demand-forecast and wind notebooks, rather than
  parsing a B1610 date field that was never confirmed to exist under
  any particular name.
- A real bug caught before it shipped, not after: the first draft
  used `__import__("datetime").timedelta(...)` inside a cell rather
  than a proper import -- the same anti-pattern flagged in ADR 0015's
  consequences for a different notebook. Fixed by adding `datetime`/
  `timedelta`/`timezone` to this notebook's own top-level imports
  cell, where they belonged from the start. Also cleaned up two local
  re-imports of `spread_by_bin` that should have just used the
  already-imported top-level name.
- A real bug caught by the tests themselves, not written correctly
  the first time: the initial Section 3 test suite reused
  `_DC_CSV_ROWS` (built earlier for testing vintage-filtering, which
  deliberately has two *vintages* of the *same* target date) where it
  needed two genuinely distinct target dates instead -- both loss
  comparison tests failed with 6 rows instead of the expected 12
  until a dedicated fixture (`_DC_CSV_ROWS_TWO_DAYS`) was added.
- Proven directly, not just structurally: an irrelevant BMU (a gas
  generator, not matched by either ID pattern) with a *larger* output
  than either real candidate never leaks into the loss computation
  (`test_unrelated_bmu_never_appears_in_loss_computation`) -- the
  filtering step is doing real work, not passing everything through.
- The interconnector sign convention remains genuinely unconfirmed.
  If it turns out wrong when run against real data, import and export
  loss would be swapped -- worth checking against a known real flow
  day before trusting the DC-Low/DC-High assignment.
