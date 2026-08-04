# wattstack roadmap

Status: pre-v1. This file is the running plan -- update it as scope
actually changes, don't let it drift from what's true. Each item
should link back to an ADR once real design decisions get made for
it, the same way several already do (`docs/adr/0001`-`0007`).

## v1 -- first release

**Goal:** an installable app that triggers a day-ahead BESS
optimization against real GB market data -- Dynamic Containment Low
and High, wholesale, and the Balancing Mechanism -- with an honest
account of how it would have performed historically using the
forecast vintages that were actually available at decision time, not
hindsight. Still not a production trading system or a promise of
achievable returns -- an educational tool that happens to be built
properly enough to trust its own numbers.

v1 is bigger than the original scope (below) and won't be built in one
pass. Phased so each phase is independently useful and testable,
matching "start simple, then add complexity" -- the phases are the
simple-first plan, not an excuse to defer honesty about total scope.

### Phase A -- market model correction (core, no new dependencies)

**Done -- see `docs/adr/0008`.** Core (`core: 26 tests`, `web: 6
tests`, all passing) and the web UI both updated; CLI and live server
verified against the new market set.

- [x] `MarketSpec` registry replacing `RESPONSE_DELIVERY_HOURS` /
      `RESPONSE_MARKETS` -- market definition becomes data (name,
      direction, delivery_hours, settlement_unit), not scattered
      conditionals. This is the actual answer to "how do I add a
      market beyond DC/wholesale/BM": add a registry entry.
- [x] Dynamic Containment split into `dc_high` (triggered by high
      frequency -- the system has excess, the battery absorbs --
      charge-direction, needs footroom, a new constraint) and
      `dc_low` (triggered by low frequency -- the system is short,
      the battery injects -- discharge-direction, needs headroom, the
      constraint that already exists). Corrected mid-design, worth
      being direct about rather than quietly fixing: an earlier
      version of this roadmap had the two directions backwards.
      Confirmed via NESO's own `DCH`/`DCL` tags that these are
      genuinely opposite-direction products, not one product under
      two names. Regression-tested directly so this can't silently
      flip back.
- [x] Wholesale and Balancing Mechanism as distinct market entries
      (pulls forward part of the old backlog item 1 below). BM is two
      registry entries, `bm_offer`/`bm_bid`, structurally in place now
      -- still priced by `SyntheticPriceProvider` placeholders, not
      the real LOLP-calibrated proxy, which stays Phase B work below.

### Phase B -- real, vintage-aware data
- [ ] `ForecastProvider` protocol (mirrors `core`'s `PriceProvider`):
      `as_of(publish_time) -> dict`. Live runs call `as_of(now)`;
      backtests call `as_of(historical_trigger_time)` -- same code
      path, which is what makes the backtest trustworthy rather than
      a parallel simulation that can drift from what actually runs.
- [ ] At least one real implementation wired end to end: Elexon demand
      forecast, confirmed via `demand_forecast_api.py` to support
      `/forecast/demand/day-ahead/history?publishTime=X` (also
      `earliest`/`latest`/`evolution` variants). NESO's DC 4-day
      forecast has a confirmed separate "History" resource, needed for
      the same reason.
- [ ] Generation, LOLP/margin, surplus, and indicated-generation
      forecasts (`system_forecast_api.py`, `surplus_forecast_api.py`,
      `indicated_forecast_api.py`) -- very likely the same
      `history?publishTime=` pattern (same auto-generated Insights
      client), NOT individually confirmed yet. Verify each before
      relying on it, same discipline as the rest of `ingestion/`.
- [ ] Real energy and DC prices wired into `core` (`ElexonPriceProvider`,
      `NesoPriceProvider`) -- carried over from the original v1 scope.
- [ ] Real, LOLP-calibrated pricing for `bm_offer`/`bm_bid` -- the
      registry entries themselves are already built (Phase A,
      `docs/adr/0008`), directional and structurally correct
      (`bm_offer` discharge, `bm_bid` charge, mirroring DC-High/
      DC-Low), but still priced by `SyntheticPriceProvider`
      placeholders. BM prices/volumes genuinely cannot be forecast
      day-ahead -- what's proposed instead is a calibrated *tightness
      proxy*, not a real forecast:
      - Use LOLP (and/or margin/surplus) forecasts, already on the API
        list, as a forward-looking system-tightness signal.
      - Calibrate against real historical outturn: bucket by LOLP
        decile, and for each bucket look at the realised probability
        and price distribution of `classify_system_length()` (already
        built, already validated against Elexon's own published
        SPAR figures) -- Short periods historically command higher
        System Prices than Long ones, which is exactly the asymmetry
        `bm_offer` vs `bm_bid` needs to reflect.
      - This is an empirical relationship, not a formula to guess at
        -- explore and validate it in a new marimo notebook (same
        "explore, then promote" workflow as everything else in
        `ingestion/`) before any of it reaches the live optimizer.
      - Real limit, stated plainly: this improves *how much capacity
        the day-ahead plan reserves* for probable BM upside -- it does
        not make BM day-ahead-schedulable in the literal sense. Actual
        BM bid execution is real-time and stays a separate, later
        concern (see the open question in Phase C above).
      - `delivery_hours` default: one settlement period (0.5h), same
        granularity as wholesale -- a simplification, worth revisiting
        once real acceptance-duration data (already fetchable via
        `bid_offer_acceptances`) is actually examined.

### Phase C -- the app itself
- [ ] `wattstack serve` -- pip-installable console script wrapping
      `runserver` + opening a browser tab. "Install and trigger,"
      honestly, without new packaging technology (PyInstaller etc.
      stays out of scope for v1).
- [ ] `wattstack run-day-ahead` -- headless trigger, same underlying
      run function the UI's "run now" button calls. Cron-able once
      trusted.
- [ ] Trigger timing: a single run shortly after N2EX gate closure
      (confirmed 09:50 GMT, results by 10:00 GMT), not a continuous
      scheduler. After gate closure, wholesale prices for tomorrow are
      published fact, not a forecast -- only DC-Low/DC-High (from that
      day's 4-day forecast) and BM genuinely need forecasting.
- [ ] Open design question, not resolved here: BM can't be known
      day-ahead at all. Does the day-ahead run produce a fixed
      schedule that simply stays open to unplanned BM opportunities
      (leaning this way for v1 -- simpler, honest about what a
      day-ahead tool can promise), or reserve flexibility for a second,
      closer-to-real-time pass? Revisit with real data in hand.
- [ ] "How this works" panel in the UI, updated for the real v1 scope
      -- which forecast vintage fed a given run, what's still
      approximate, stated in the app, not just in an ADR a developer
      would read.

### Phase D -- backtest and sensitivity
- [ ] Vintage-forecast backtest harness: for each historical day, get
      the forecast vintage that would have been available at the
      historical trigger time, run the same optimizer against it, then
      score the resulting schedule against real outturn data. The gap
      between that and a perfect-foresight run on the same day is the
      honest "cost of imperfect forecasting" number.
- [ ] Solution-explainer / sensitivity report per run: shadow prices on
      binding constraints (SOC floor/ceiling, power cap) -- PuLP
      exposes LP duals, worth validating cleanly in this model before
      relying on it -- plus parametric sweeps (power/duration/
      efficiency +/-) reusing the existing `run_sweep()`.

Quickstart docs aimed at a non-developer user, and the optional static
Capacity Market calculation, both carry over from the original scope
and land wherever they're cheapest once the above exists.

## Backlog, beyond v1

### Reserve services (BR/QR/SR) alongside response
The rest of the old "BM + wholesale + reserve stacking" item -- BM and
wholesale separation moved into v1 Phase A above; adding reserve
services is real future work once the `MarketSpec` registry (Phase A)
makes it a data change rather than a code change.

### Rolling horizon + imperfect price capture, beyond the vintage backtest
The vintage backtest (Phase D) answers "how would today's day-ahead-
only decision have performed" -- it does not yet model intraday
adjustment, execution slippage, or minimum bid granularity. Real
follow-up work once Phase D exists to build on.

### Skip rates
Needs real acceptance-rate data against a real BM stream (Phase A) --
natural extension of the marginal-bid-share pattern already proven out
in `ingestion/analysis.py`.

## Known limitations already tracked elsewhere

Don't duplicate these here:
- `docs/adr/0002` -- optimizer simplifications (continuous LP, daily-
  independent solves, PuLP/CBC choice)
- `docs/adr/0004` -- ingestion built against researched, not fully
  live-tested, API shapes
- `docs/adr/0006` -- BOALF is volume, not price; BOD is price
- `docs/adr/0007` -- BESS not identifiable by fuelType alone
- `docs/adr/0008` -- MarketSpec registry, and correcting DC-High/
  DC-Low direction (Phase A, implemented)
