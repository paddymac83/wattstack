# 13. Wind forecast as a volatility signal, not a direction signal

Date: 2026-08-07

## Status
Accepted -- implemented

## Context
Following the LOLP negative result (ADR 0012), wind was the proposed
next candidate -- wind forecast error is the actual dominant driver of
short-term GB balancing noise, unlike LOLP's coarse adequacy measure.

Confirmed directly from Elexon's own API documentation (more
authoritative than the Python client wrapper used for demand and
LOLP, since it describes the real dataset rather than an
auto-generated client's method signatures): wind generation forecast
(WINDFOR) has a genuine `/forecast/generation/wind/history` endpoint
-- unlike LOLPDRM, wind fits `ForecastProvider` properly. Also
confirmed: NESO publishes it up to 8 times a day, at fixed times
(03:30, 05:30, 08:30, 10:30, 12:30, 16:30, 19:30, 23:30). The
day-ahead trigger (10:00 UTC) falls just after the 08:30 publication
-- a real, checkable implication of that schedule, not a guess.

One thing genuinely different this time, worth being honest about:
the `publishTime` query parameter name on `/forecast/generation/wind/history`
was NOT independently confirmed the way demand forecast's was (that
came from reading `demand_forecast_api.py`'s source directly).
Elexon's documentation described the endpoint's existence and
behaviour but not its parameter names. `publishTime` is used because
it's the repeated convention on every other confirmed `/history`
endpoint in this module -- a strong pattern, not a certainty for this
specific one.

Separately, and more importantly for what got built: the request was
explicitly for wind forecast as a **volatility** signal, not a
direction signal. Demand and LOLP were both tested against
`classify_system_length()` -- does the variable predict which way
(Long/Short) a period leans. Volatility is a different question: does
the variable predict how *spread out* prices get, regardless of
direction. Reusing `bin_counts_by_group()` (built for counting
occurrences per group) would have quietly answered the wrong question
by defaulting to the pattern already on hand.

## Decision
- `ElexonClient.wind_forecast()` / `wind_forecast_history()` /
  `verify_wind_forecast_schema()`, same `params=` discipline as
  everything else (ADR 0011).
- `ElexonWindForecastProvider` -- the second real `ForecastProvider`
  implementation (after demand, ADR 0009), and the first one built
  since LOLP showed a forecast doesn't automatically fit the
  protocol just because it sounds similar.
- New analysis primitive, `spread_by_bin()`: bins one variable and
  computes the mean and standard deviation of a second variable
  within each bucket -- dispersion, not occurrence counts. Genuinely
  different from `bin_counts_by_group()`, not a renamed copy of it.
- New chart, `spread_chart()`: mean with error bars, sample size
  carried in hover text specifically so a wide error bar on 2
  observations isn't visually indistinguishable from one on 200.
- `notebooks/demand_forecast_vs_system_tightness.py` gained a third,
  additive section. Wind uses the identical day-ahead trigger
  convention as demand (10:00 UTC the day before) rather than a
  special time tied to wind's own 8x/day schedule -- the `history`
  endpoint resolves "what was known at 10:00" to whichever real
  publication preceded it; the notebook doesn't need to encode wind's
  publication schedule itself. Both the volatility chart (what was
  asked) and a secondary direction chart (cheap, given the plumbing
  already exists) are included, with the secondary chart explicitly
  labelled as answering a different question, not a more complete
  answer to the same one.

## Consequences
- The mocked test suite proves the *mechanism* -- correct trigger
  time, correct join, and (via deliberately shaped mock data) that
  `spread_by_bin()` genuinely distinguishes a tightly-clustered
  bucket from a widely-scattered one. It cannot prove wind forecast
  *actually* predicts volatility in reality -- that's the same
  standing limit as every other notebook in this package: a real
  finding needs a real run, the same way LOLP's rejection needed real
  winter/summer data, not just a working pipeline.
- `spread_by_bin()` and `spread_chart()` are generic, not wind-
  specific -- reusable for any future "does X predict dispersion in
  Y" question, the same way `bin_counts_by_group()` turned out to be
  reusable across demand, LOLP, and now wind's secondary chart.
- The unconfirmed `publishTime` parameter name is a real, named risk,
  not a silent one -- `verify_wind_forecast_schema()` exists
  specifically so a wrong assumption here fails loudly (via a live
  400 or empty response) rather than quietly returning nothing
  useful.
