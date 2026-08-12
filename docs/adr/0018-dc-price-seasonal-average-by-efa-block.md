# 18. DC price via seasonal average by EFA block, mirroring the wholesale approach

Date: 2026-08-09

## Status
Accepted -- implemented

## Context
Following the confirmed-working wholesale price provider
(`docs/adr/0017`), DC-High and DC-Low were next per the fast-path-to-v1
plan. Same honesty requirement applies: no real forecast of DC
clearing price exists, so this is a seasonal average of real
historical data (`response_reserve_results_summary`, already
confirmed reachable), not a predictive model. The inertia/loss-
calibrated model from the earlier exploratory work (ADR 0015, 0016)
remains a real, deliberately deferred future upgrade -- this is the
same "start simple" choice already made for wholesale and for DC's own
requirement-volume analysis.

Two real gaps found and closed before building the provider, not
discovered by using it:

1. `NesoClient.datastore_search()` had no `sort` parameter. CKAN's
   `datastore_search` action supports one natively (standard CKAN API
   behaviour, not project-specific), but this project had never used
   it. Without an explicit sort, a plain `limit=N` fetch against a
   dataset covering November 2023 onwards has no guaranteed
   relationship to recency -- could return the oldest N records as
   easily as the newest. Added `sort` as an optional parameter,
   backward compatible with every existing call site.
2. DC clears per EFA block (6 per day), not per settlement period
   (48, wholesale's granularity). `efa_block_number_for_hour()` (new,
   `analysis.py`) is the inverse of the already-existing
   `efa_block_label_for_index()` -- needed because DC results give a
   delivery timestamp, not an EFA1..EFA6 column the way the DC
   requirements CSV did. Proven to genuinely be the inverse by test
   (`test_efa_block_number_for_hour_is_the_genuine_inverse_of_efa_block_label_for_index`),
   not just individually correct.

Field names inside a `response_reserve_results_summary` row --
which field identifies DC-High vs DC-Low, its exact string values, and
which field holds the clearing price -- were not independently
confirmed. Only `deliveryStart`/`deliveryEnd` being real UTC datetime
fields was previously confirmed (NESO's own EAC results page).
Everything else is a reasoned guess, same treatment as
`ElexonWholesalePriceProvider`'s `period_field`/`price_field`.

## Decision
- `NesoClient.datastore_search()` gained an optional `sort` parameter,
  used as `f"{delivery_start_field} desc"` by the DC provider
  specifically to guarantee "most recent" actually means recent.
- `efa_block_number_for_hour()`, a new generic `analysis.py`
  primitive, complementing `efa_block_label_for_index()`.
- `NesoDCPriceProvider` (`ingestion/wattstack_ingestion/prices.py`),
  implementing `reserve_prices(day, market)` for `DYNAMIC_CONTAINMENT_HIGH`/
  `DYNAMIC_CONTAINMENT_LOW` only -- raises `ValueError` for any other
  market. Dispatches on `market.name` rather than importing
  `core.markets.Market`, preserving the standing rule that `ingestion`
  doesn't depend on `core` (ADR 0009) -- proven directly by test using
  a lightweight stand-in object, not the real enum.
- The averaging shape is genuinely different from wholesale's: DC's 6
  EFA-block averages (via `seasonal_average_by_period()`, reused
  unchanged, with EFA block number as the "period") get broadcast
  across the 8 settlement periods each block covers, not returned
  directly as 6 values. Proven by test that a single EFA block's
  average appears correctly across all 8 of its periods, and that an
  EFA block with no data falls back to 0.0 independently of blocks
  that do have data.
- Field names (`service_type_field`, `price_field`,
  `delivery_start_field`, `dc_high_value`, `dc_low_value`) are all
  constructor parameters with reasoned-guess defaults, correctable
  without a code change once `verify_schema()` shows the real ones --
  same pattern as the wholesale provider, applied consistently.
- Excludes the target day itself from the lookback window, same
  design property as wholesale's provider, proven the same way (by
  test, not just documented).

## Consequences
- **Confirmed live, a real correction, not a refinement:** the field
  that actually distinguishes DC-High from DC-Low is `auctionProduct`
  (values `DCH`/`DCL`), not `serviceType` as originally guessed.
  `serviceType` is a real field, but holds a broader, unrelated
  category ("Response", "Slow Reserve") -- the original guess would
  have matched zero rows for either market. Fixed by switching the
  filter to `auction_product_field`, and the now-unused
  `service_type_field` parameter was removed entirely rather than
  left in place unused -- a parameter that looks configurable but does
  nothing is worse than no parameter, since it invites a future caller
  to "fix" behaviour by changing something that has no effect.
- **`CombinedPriceProvider` added**, a thin delegator composing a
  wholesale provider and a reserve provider into one object satisfying
  core's full `PriceProvider` protocol -- `optimize_day()` takes a
  single price_provider argument, not one per market, so running a
  real stacked wholesale+DC optimization needs something that answers
  both `wholesale_prices()` and `reserve_prices()`. Deliberately not a
  merge of logic: each specialised provider stays independently simple
  and testable, proven by test that calling one method never touches
  the other provider
  (`test_wholesale_and_reserve_calls_are_independent`). Only wraps one
  reserve provider for now, since only DC has one built -- will need
  to route by market once a BM provider exists, not solved here.
- The wholesale provider's `verify_mid_schema()`-first discipline
  applies here too, explicitly -- this provider has not been checked
  against a real response yet. `NesoClient.verify_schema()` (already
  built, defaults to `response_reserve_results_summary`) is the right
  first step before trusting `service_type_field`/`price_field`/
  `dc_high_value`/`dc_low_value`'s defaults, the same way
  `verify_mid_schema()` was for MID.
- `NesoDCPriceProvider` and `ElexonWholesalePriceProvider` are
  deliberately separate classes with disjoint protocol coverage
  (`reserve_prices()` only vs `wholesale_prices()` only) -- pairing
  both together via `CombinedPriceProvider` satisfies the full
  `PriceProvider` protocol for a wholesale + DC stacked run; BM's
  price provider remains unbuilt, matching the roadmap's fast-path
  scoping.
- The `sort` parameter addition to `datastore_search()` is backward
  compatible and available to every existing NESO method, not just
  this new provider -- a real capability gain for the whole client,
  not scoped narrowly to DC.
