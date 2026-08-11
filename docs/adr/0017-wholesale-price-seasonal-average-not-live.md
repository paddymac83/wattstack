# 17. Wholesale price via seasonal average of MID, not live data -- pre-gate-closure triggers can't use settled prices

Date: 2026-08-08

## Status
Accepted -- implemented

## Context
The original fast-path-to-v1 plan (ROADMAP.md, 2026-08-08 pivot) named
MID (Market Index Data) as the wholesale price source, on the
reasoning that it's the best real proxy available for wholesale
short-term trading. Corrected the same day, before any code was
written: the actual requirement is to optimize *before* N2EX's
day-ahead gate closure (09:50) -- meaning the optimization decision
happens before tomorrow's wholesale price exists as a settled fact.
MID is realised/settled data, not a forecast. Using it as a live input
for a future day would be the same category of error `system_prices()`
would be for the same purpose: real data, wrong tense.

No dedicated "wholesale price forecast" dataset was found anywhere in
this project's research (unlike demand, wind, LOLP -- all genuinely
forecast by NESO). Wholesale price is a market-clearing auction
outcome, not a physical quantity NESO forecasts. Building a genuinely
predictive model (using demand/wind forecasts as explanatory inputs)
is real, substantial work, and the roadmap's own "start simple" pivot
argues against attempting it as the first wholesale implementation.

MID's actual query parameters were not independently confirmed at
first the way B1610's eventually were. Initially guessed as
`settlementDate`/`settlementPeriod`, reasoned from the pattern every
other confirmed opinionated endpoint in this project follows
(`bid_offer_acceptances`, `bid_offer_data`, `disaggregated_bsad`, and
B1610 once corrected) -- a considered inference, not a guess pulled
from thin air, but wrong nonetheless. Confirmed live, the same day:
the real endpoint is `/datasets/MID` with `from`/`to`/`dataProviders`,
a genuine date-range shape, not per-period. See Consequences below for
the correction in full -- recorded here honestly rather than silently
rewriting this ADR as if the first guess had been right.

A real, sourced MID data-quality issue was found during research and
built around from the start rather than discovered later: N2EX shows
all-zero values for some dates.

## Decision
- `ElexonClient.market_index_data()` / `market_index_data_range()` /
  `verify_mid_schema()`, built on the confirmed real endpoint
  (`/datasets/MID`, `from`/`to`/`dataProviders`), `params=` throughout
  (ADR 0011's rule). `market_index_data()` is a thin wrapper matching
  a single confirmed request; `market_index_data_range()` chunks any
  longer window into `<=7-day` requests, confirmed live to be MID's
  own range limit -- MID is a genuine date-range endpoint, but not an
  unlimited one, unlike the initial assumption.
- `seasonal_average_by_period()`, a new generic `analysis.py`
  primitive: average value per settlement period, pooled across
  whatever historical rows are given, with zero-value exclusion by
  default (disableable, since that default is specific to MID's known
  issue, not necessarily right for every future caller).
- `ElexonWholesalePriceProvider` (`ingestion/wattstack_ingestion/prices.py`,
  new module, same placement reasoning as `forecasts.py`: structural
  typing means it doesn't need to import `core`, and `core` doesn't
  need to import it). `wholesale_prices(day)` calls
  `market_index_data_range()` for a rolling lookback window (default
  28 days) ending the day *before* `day` -- never `day` itself -- and
  returns the pooled seasonal average. Proven directly by test
  (`test_wholesale_prices_range_excludes_the_target_day_itself`) that
  the fetched range never includes the target day, not just
  documented as the intent.
- Field names (`period_field`/`price_field`) are explicitly named as
  unconfirmed in code, not just in this ADR -- constructor parameters
  specifically so a wrong guess can be corrected without a code
  change, once `verify_mid_schema()` reveals the real ones.
- Deliberately implements only `wholesale_prices()`, not the full
  `PriceProvider` protocol. `reserve_prices()` (DC, BM) is separate,
  explicitly out of scope for this ADR.

## Consequences
- **Confirmed live, a real mistake corrected, not a detail refined:**
  the endpoint and parameters used above were wrong. MID is actually
  `/datasets/MID` with `from`/`to`/`dataProviders` (confirmed against
  a real, working request URL) -- not the `/balancing/pricing/market-index`
  endpoint with `settlementDate`/`settlementPeriod` first assumed.
  The reasoning that led to that guess ("every confirmed opinionated
  endpoint uses settlementDate/settlementPeriod, so that's the safer
  bet than a wrapper's from/to suggestion") turned out wrong for this
  specific endpoint -- worth stating plainly: pattern inference from a
  handful of other endpoints is not a substitute for checking the
  specific one, no matter how consistent the sample looks. Fixed by
  rewriting `market_index_data()` around the confirmed shape, with a
  regression test pinning the exact confirmed URL
  (`test_market_index_data_matches_the_real_confirmed_url_shape`) so
  this specific mistake can't quietly return.
- **A real efficiency win came out of the correction, not just a fix:**
  MID turned out to be a genuine date-range endpoint -- one call
  covers a full day (confirmed), and, before the 7-day limit was also
  found, appeared to cover an unlimited window at once. That's now
  correctly bounded (see the second correction below), but the shape
  is still a real efficiency win over B1610's per-period-only design:
  `ElexonWholesalePriceProvider.wholesale_prices()` costs a handful of
  chunked requests for a typical lookback window, not one per day.
- `dataProviders` is a list parameter (confirmed: multiple providers,
  e.g. `["N2EXMIDP", "APXMIDP"]`, can be requested together) -- kept
  configurable on the provider rather than hardcoded to N2EX only,
  even though N2EX is the sensible default.
- Field names inside a MID row remain genuinely unconfirmed -- only
  the endpoint and query parameters were checked live this time, not
  the response schema. `verify_mid_schema()` is still the right next
  step before trusting `period_field`/`price_field`'s defaults.
- **Second correction, confirmed live, same day:** MID also only
  allows a 7-day span per request. `market_index_data_range()` fetches
  any longer window in <=7-day chunks automatically, and
  `ElexonWholesalePriceProvider` was updated to use it -- the default
  28-day lookback now costs 4 chunked requests, not the single
  (invalid, would have failed) call the earlier version assumed.
  Proven by test that chunking is gapless and non-overlapping
  (`test_market_index_data_range_covers_the_full_range_with_no_gaps_or_overlaps`),
  not just that it makes the right number of calls.
- **The zero-value question above is now resolved, confirmed live:**
  N2EXMIDP specifically showed zero price and volume for the most
  recent several days, while APXMIDP for the identical dates showed
  real, non-zero data. That rules out a general MID reporting lag
  (which would affect both providers equally for the same dates) --
  this points to an N2EX-specific feed issue into MID, not a
  settlement-timing one. Default `data_providers` changed from
  `["N2EXMIDP"]` to `["APXMIDP"]` throughout (`ElexonClient.market_index_data()`
  and `ElexonWholesalePriceProvider` both) -- confirmed live to
  actually return data, not chosen for "N2EX is the more liquid GB
  exchange" general market knowledge the way it was originally picked.
  That reasoning turned out not to matter once live data showed which
  provider was actually reporting.
- The regression test pinning the original confirmed URL
  (`test_market_index_data_matches_the_real_confirmed_url_shape`) now
  passes `data_providers=["N2EXMIDP"]` explicitly, matching the real
  URL exactly, rather than relying on the default -- keeps the test
  accurate to what was actually confirmed regardless of which provider
  the default points to later.
- **Nord Pool and EPEX SPOT do not offer a free, publicly accessible
  API for day-ahead auction data.** Checked directly, from multiple
  independent sources: EPEX SPOT's own data shop gates historical
  auction results behind a paid, annually-invoiced membership
  subscription; a third-party project built specifically to work
  around this gap states plainly that neither exchange offers free
  API access for non-commercial use. Real access exists via a paid
  EPEX SPOT/EEX Group subscription, or a paid third-party aggregator
  (Modo Energy, Montel both sell this commercially) -- Modo Energy in
  particular is one of the commercial platforms this whole project has
  been positioned as a transparent, free alternative to; using their
  feed would be a deliberate departure from that principle, not a
  quiet substitution. Not pursued further here -- MID (via Elexon,
  free) remains the wholesale price path unless a paid source is
  explicitly chosen later.
