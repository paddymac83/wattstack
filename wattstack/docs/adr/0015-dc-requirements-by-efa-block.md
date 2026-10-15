# 15. DC requirement forecasts by EFA block, and a new CSV access pattern

Date: 2026-08-07

## Status
Accepted

## Context
Focus shifted to Dynamic Containment specifically: DC-L and DC-H
*requirement* volumes (how much NESO needs to procure), not price --
genuinely different data from anything built so far, which has all
been price/acceptance data or demand-side forecasts.

Confirmed directly from NESO's own page
(neso.energy/data-portal/dynamic-containment-4-day-forecast, August
2026), not assumed: *"The methodology uses forecasted demand,
inertia, and response volumes as well as a view of the largest losses
on the system to estimate the DC requirements."* Four inputs, not
three -- demand is explicitly named alongside inertia, largest
losses, and other response volumes. Also confirmed directly:
*"changes to interconnector flows from our forecasted position can
lead to either an increase or decrease in our requirements if the
change impacts the largest loss we need to secure"* -- ties the
largest-loss driver explicitly to interconnector import/export.

Two access-pattern findings, both real, both changing what got built:

1. **The DC requirements dataset is CSV-download-only, not
   datastore-active** -- no `datastore/dump/` link on NESO's page,
   only `download/*.csv`. This is the same situation `neso.py`'s
   existing docstring already flagged for a different NESO dataset
   (the four "Daily ..." resources on the EAC results page) -- now
   confirmed to also apply here, and actually built rather than left
   as a flagged gap. `fetch_csv()` is the first genuinely CSV-based
   NESO method in this project; every other NESO method goes through
   `datastore_search`'s JSON API.
2. **No confirmed separate "inertia forecast" dataset exists with a
   public endpoint.** "System Inertia" is a real, confirmed,
   datastore-active dataset -- but it's outturn (what inertia
   actually was), not a forward forecast. NESO's own "GB Inertia
   Forecasting" page describes an internal methodology/research
   project, not an obviously downloadable dataset. Testing the
   inertia hypothesis with outturn-vs-outturn (both from history
   resources) is an honest substitute, not the same thing as matching
   forecast vintages the way demand/wind forecasts do elsewhere in
   this project.

EFA blocks (6 blocks, 4 hours each, starting 23:00/03:00/07:00/
11:00/15:00/19:00) confirmed via a real NESO Frequency Response
Market Information Report (April 2023), not assumed from general
market knowledge.

No confirmed dataset was found for "largest secured loss" or "other
response product levels" as their own structured, fetchable
resources -- genuinely unresolved, not silently dropped. The DC
requirements CSV's own schema is the first place to check whether
either is exposed as a column there once fetched for real.

## Decision
- `NesoClient.fetch_csv()` -- generic CSV fetch-and-parse, cached by
  URL like everything else here is cached by resource_id.
- `dc_requirements_forecast_current()` / `_history()`, both built on
  `fetch_csv()` against the two confirmed download URLs.
- `system_inertia(resource_id, ...)` built on the *existing*
  `datastore_search()` -- takes the resource_id explicitly rather
  than defaulting to one, since only 2019-2020 through 2024-2025 were
  individually confirmed; no 2025-2026 resource was found, though the
  consistent yearly pattern makes one likely.
- `EFA_BLOCKS`, `efa_block_label()`, and `efa_block_label_for_index()`
  live in `analysis.py`, not `neso.py` -- generic, testable
  classification logic belongs with the rest of `analysis.py`'s
  functions, not duplicated into the client module that happens to be
  the first consumer of it. Two functions, not one, because the real
  data (see Consequences below) gives the EFA block as a column index
  (`EFA1`..`EFA6`), not an hour to derive it from -- `efa_block_label_for_index()`
  was added once that was confirmed live, not anticipated in advance.
- `notebooks/dc_requirements_by_efa_block.py`: Section 1 answers the
  literal ask (DC-H/DC-L requirement variation across EFA blocks, via
  `spread_by_bin`-style mean+std-dev per block, reusing `spread_chart`
  from the wind-volatility work rather than building a new chart
  type) -- reshaped from the real wide-by-EFA-column, long-by-service
  format via unpivoting `EFA1`..`EFA6`, not the flat one-row-per-period
  shape this ADR originally assumed. Section 2 tests the inertia
  hypothesis against outturn data, additive and independently gated,
  same pattern as every other multi-section notebook in this project.

## Consequences
- **Correction, confirmed live, more substantial than a field-name
  detail:** the real CSV is not one row per settlement period with
  separate DC-High/DC-Low columns, which is what this ADR and the
  notebook originally assumed. It's `Forecast_Created`,
  `Forecast_Target_Date`, `Service_Type`, and six columns `EFA1`..`EFA6`
  holding the requirement directly per block -- long-format across
  service type, wide-format across EFA block. The reshaping logic was
  rewritten accordingly: `efa_block_label_for_index()` (new, in
  `analysis.py`) maps NESO's own EFA1..EFA6 numbering to the same
  label format `efa_block_label()` produces from an hour -- confirmed
  to agree at every block boundary
  (`test_efa_block_label_for_index_matches_efa_block_label_at_block_starts`).
- **The "no vintage mechanism" claim below was wrong, corrected the
  same day it was written.** `Forecast_Created` vs
  `Forecast_Target_Date` means the file genuinely does carry a vintage
  dimension -- just not as a query parameter the way Elexon's
  `/history` endpoints work; it's a filterable column in the data
  itself. The notebook now has an explicit, visible toggle
  (`latest_vintage_only`, defaulting on) rather than silently picking
  one interpretation -- multiple forecast revisions of the same
  target date would otherwise inflate the apparent EFA-block variation
  with revision noise, not genuine market variation. Proven both ways
  by test: with the toggle on, only the latest `Forecast_Created` per
  target date survives; with it off, both vintages do.
- Same day-first date bug (see the entry above) applies to
  `Forecast_Created` and `Forecast_Target_Date` too, not just the
  single datetime field the earlier version assumed -- both parsed
  with `dayfirst=True`, both covered by
  `test_day_first_target_date_parsed_correctly`.
- `Service_Type`'s actual string values (confirmed live to exist, not
  confirmed which string means which service) are still resolved via
  the same interactive-discovery dropdown pattern used everywhere else
  in this project for unconfirmed field *values* -- the difference
  from field *names* is deliberate: NESO's column names are now
  trusted directly (confirmed live), but what's inside `Service_Type`
  is not.
- **Confirmed live, same day (not by anything in this notebook's own
  tests until after the fact):** the DC requirements CSV's date field
  is day-first (`01/06/2026` = 1 June, not January 6). Fixed with
  `pd.to_datetime(..., dayfirst=True, ...)` in both the DC requirements
  and inertia date-parsing cells (the inertia fix is defensive, not
  independently confirmed for that field -- but `dayfirst=True` has no
  effect on unambiguous ISO-format dates, so it's safe either way).
  `test_ambiguous_dates_parsed_as_day_first_not_month_first` proves
  this two ways: it fails without the fix (confirmed by temporarily
  reverting it), and it deliberately checks the parsed month/day
  rather than the EFA block label, since the label alone doesn't
  reveal a day/month swap -- the hour is identical either way.
- **A real debugging lesson, worth recording since it'll recur:**
  `pdb`/`breakpoint()` doesn't see marimo's underscore-prefixed
  cell-local variables (`_row`, `_parsed`, etc.) by their source
  names -- marimo's cell-isolation model transforms how they're
  stored, and `pdb`'s frame inspection doesn't follow that
  transformation. `pp locals()` at the breakpoint, or temporarily
  renaming the variable without a leading underscore, both work
  around it. Worth knowing before debugging any notebook in this
  project, not just this one.
- A real, caught-in-testing bug worth naming: the initial EFA-block
  chart cell used a hacky `__import__("pandas")` walrus expression to
  work around a missing cell dependency, rather than just declaring
  `pd` properly. Fixed before shipping, not left in --
  `mo`'s dependency-injection model makes this kind of shortcut
  tempting when a cell needs one more import; the honest fix is
  always to declare the dependency, not route around the graph.
- Proven directly, not just structurally: EFA block aggregation
  correctly combines the same block across different calendar days
  (`test_efa_block_aggregation_combines_rows_across_different_days`)
  -- the actual point of the analysis, not incidental to it.
- Largest-loss and other-response-level data remain genuinely
  unconfirmed. Not built around a guess; left as a real gap for
  whoever runs this against real data to check via the schema preview
  cells.
- Connects to the acceptance-risk thread already on the roadmap, not
  built here: DC is pay-as-clear, so requirement volume is naturally
  an acceptance-probability signal (higher requirement -> more
  capacity clears -> lower rejection risk for a given bid), not a
  price-level signal. Worth remembering when that thread gets picked
  up; this ADR doesn't implement it.
