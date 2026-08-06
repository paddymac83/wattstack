# 9. ForecastProvider lives in ingestion, not core; first implementation is Elexon demand forecast

Date: 2026-08-05

## Status
Accepted -- implemented (ROADMAP.md v1 Phase B, first item)

## Context
Phase B needed a vintage-aware way to answer "what was known at time
X" -- the mechanism a backtest depends on to be trustworthy rather
than a parallel simulation that can drift from what actually runs.
The roadmap described this as mirroring `core`'s `PriceProvider`
protocol, which raised a real placement question: does the protocol
itself belong in `core`, next to `PriceProvider`, or somewhere else?

`core`'s optimizer consumes `PriceProvider` directly -- it's a
parameter type in `optimize_day()`. It does not consume forecasts at
all yet; nothing in `core` needs to know a `ForecastProvider` exists.
Putting the protocol in `core` anyway would mean designing an
abstraction ahead of an actual consumer, the same YAGNI reasoning
that's kept `Capacity Market` defined but unwired since the project's
early days.

Separately: of the six forecast APIs on the original list (DC 4-day,
demand, generation, LOLP/margin, surplus, indicated generation), only
demand forecast had actually been read from source
(`demand_forecast_api.py`), not just documentation. Re-read directly
before writing any client code this time, rather than relying on
memory from a few turns earlier -- confirmed precisely: base path
`/forecast/demand/day-ahead`, history variant takes a `publishTime`
query parameter (exact name), plus `earliest`/`latest`/`evolution`
variants not yet wired in.

## Decision
- `ForecastProvider` (one method, `as_of(publish_time) -> list[dict]`)
  lives in `ingestion/wattstack_ingestion/forecasts.py`, not `core`.
  Python Protocols are structural -- an ingestion-side implementation
  satisfies whatever `core` eventually needs without either package
  importing the other. `@runtime_checkable` is used specifically so
  this claim is a real `isinstance()` assertion in tests, not just an
  assumption written in a docstring.
- `ElexonDemandForecastProvider` is the first implementation, wrapping
  two new `ElexonClient` methods (`demand_forecast_day_ahead`,
  `demand_forecast_day_ahead_history`) that call the confirmed
  endpoints directly.
- `as_of()` always calls the `history` endpoint, even when called with
  `datetime.now()` -- deliberately no special-casing "now" to a
  different, simpler endpoint. A live run and a backtest run must be
  the same code path for the backtest to mean anything; a proven test
  (`test_as_of_called_with_now_uses_the_same_history_endpoint`) checks
  this directly, not just by convention.
- Field names inside a demand forecast row remain unconfirmed --
  `verify_demand_forecast_schema()` exists for the same reason
  `verify_schema()` exists elsewhere in this package: to fail loudly
  and specifically rather than silently.

## Consequences
- Nothing in `core` changed. `ForecastProvider` is additive,
  ingestion-only, and does not touch the optimizer or its tests --
  confirmed by running `core`'s suite unchanged alongside this work.
- **Update, same day:** the "nothing consumes `as_of()` yet" gap this
  ADR originally flagged is closed. `notebooks/demand_forecast_vs_system_tightness.py`
  is a real consumer: for each historical day, it calls `as_of()` at
  10:00 UTC the day before (the day-ahead trigger window from
  ROADMAP.md Phase C) and joins the result against real settlement
  outturn (`system_prices()` + `classify_system_length()`) to ask
  whether forecast demand actually predicts Long vs Short. Proven by
  test that the trigger time is exactly right, not approximately --
  `test_trigger_time_is_exactly_1000_utc_the_day_before` and its
  multi-day sibling check the literal `publishTime` query string.
  This is deliberately the simple predecessor to the LOLP-calibrated
  BM proxy, not a replacement for it -- still nothing here reaches
  the live optimizer; that step still needs real data run through the
  notebook and a human decision that the relationship is worth
  trusting.
- `earliest`, `latest`, and `evolution` variants are confirmed to
  exist (same source) but not implemented -- real, scoped-out future
  work, not a gap discovered later.
- Generation, LOLP/margin, surplus, and indicated-generation forecasts
  are still unconfirmed against source, exactly as ROADMAP.md already
  says. This ADR doesn't change that status for them.
