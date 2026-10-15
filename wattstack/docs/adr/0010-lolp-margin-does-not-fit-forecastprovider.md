# 10. LOLP/margin doesn't fit ForecastProvider's shape -- stayed a plain client method

Date: 2026-08-05

## Status
Accepted

## Context
Following the demand forecast pattern (ADR 0009), LOLP/margin was
next. Read `system_forecast_api.py` directly before writing any code,
same discipline as before -- and found something that changes the
design, not just a new set of confirmed parameters.

Demand forecast's vintage mechanism is `history?publishTime=X`: one
call, one moment in time, matching `ForecastProvider.as_of()` exactly.
LOLPDRM (Loss of Load Probability and De-rated Margin) has no
`history` endpoint at all. Confirmed directly from source: a single
call to `/forecast/system/loss-of-load` returns **five forecast
horizons at once** per settlement period -- 1h, 2h, 4h, 8h, and
"12h+" ahead -- with the endpoint's own description precisely
defining what "12h+" means (the most recent forecast published 12 or
more hours before that period).

Forcing this into `as_of(publish_time)` would mean either (a)
returning all five horizons and making the caller pick, which isn't
what `as_of()`'s contract promises (a single "what was known at time
X" answer), or (b) picking one horizon inside the provider and
guessing its column name, since no response model was available to
confirm field names -- only the endpoint signature.

Real, checkable observation made possible by the day-ahead trigger
design (10:00 UTC the day before, ADR 0009): every settlement period
of the target day is at least 14 hours ahead of that trigger time. So
"12h+" is the semantically correct horizon for every period of the
day-ahead window, not a compromise picked among five options. That's
a genuine implication of the confirmed docstring, not a guess -- but
it still doesn't resolve which literal JSON key holds the 12h+ value.

## Decision
- `ElexonClient.loss_of_load_forecast(from_time, to_time, ...)` is a
  plain method, not wrapped in `ForecastProvider`. Query parameters
  (`from`, `to`, optional `settlementPeriodFrom`/`settlementPeriodTo`,
  confirmed range 1-50 inclusive -- not 1-48, because GB's autumn
  clock-change day has 50 settlement periods) confirmed directly from
  source, same as demand forecast.
- `notebooks/demand_forecast_vs_system_tightness.py` gained a second
  section rather than a new file -- the two forecasts answer the same
  question (does this signal predict Long/Short), so comparing them
  side by side in one place is more useful than two notebooks asking
  the same thing separately. The LOLP section is additive: field
  mappings for date/period/value are picked interactively (the value
  field lets you compare horizons directly, since the column names
  aren't confirmed), and `joined_with_lolp_df` builds on the existing
  `joined_df` rather than replacing it -- the demand-only comparison
  still works even before any LOLP field is chosen.
- One real efficiency difference worth recording: LOLP fetches in a
  **single bulk call** for the whole date range, unlike demand
  forecast's one-call-per-day loop (a consequence of the endpoint
  taking a `from`/`to` range natively, not a design choice on this
  project's side). Proven by test
  (`test_lolp_fetch_is_a_single_bulk_call_not_per_day`), not just
  claimed.

## Consequences
- `ForecastProvider` stays a one-shape-fits-one-case protocol for
  now. Whether it needs to grow a second method, or whether a second
  provider type is the honest answer for horizon-based forecasts, is
  a real open question -- not resolved here, deliberately deferred
  until there's a second horizon-shaped forecast (of the remaining
  three: generation, surplus, indicated generation) to compare
  against, rather than generalising from a sample of one.
- The exact field name for the "12h+" LOLP/margin value remains
  unconfirmed. The notebook's value-field dropdown is where that gets
  resolved by looking at real data, not by another round of reading
  source code -- some things only source code can tell you (the
  endpoint shape, the parameter names), and some things only a real
  response can (which column is which).
- Generation, surplus, and indicated-generation forecasts are still
  entirely unconfirmed against source. This ADR doesn't change that.
