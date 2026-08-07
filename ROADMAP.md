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
- [x] `ForecastProvider` protocol -- see `docs/adr/0009`. Lives in
      `ingestion/wattstack_ingestion/forecasts.py`, not `core` (core
      doesn't consume forecasts directly yet, so the protocol doesn't
      belong next to `PriceProvider` until something there needs it).
      `as_of(publish_time) -> list[dict]`, deliberately no special
      case for "now" -- a live run and a backtest run the same code
      path, proven by a test that checks this directly, not just by
      convention.
- [x] First real implementation: `ElexonDemandForecastProvider`,
      wrapping two new confirmed `ElexonClient` methods
      (`demand_forecast_day_ahead`, `demand_forecast_day_ahead_history`).
      Re-read `demand_forecast_api.py` directly (not from memory)
      before writing this -- confirmed precisely: base path
      `/forecast/demand/day-ahead`, `publishTime` query parameter
      name on the `/history` variant. `earliest`/`latest`/`evolution`
      variants confirmed to exist, not yet implemented.
- [x] First real consumer: `notebooks/demand_forecast_vs_system_tightness.py`
      -- for each historical day, calls `as_of()` at 10:00 UTC the day
      before (the day-ahead trigger window, Phase C above) and joins
      the result against real settlement outturn to ask whether
      forecast demand predicts Long vs Short. Deliberately the simple
      predecessor to the LOLP-calibrated BM proxy below, not a
      replacement -- still exploratory, nothing here reaches the live
      optimizer yet. See `docs/adr/0009`'s same-day update.
      **Not yet done:** NESO's DC 4-day forecast (confirmed separate
      "History" resource, same need) doesn't have a provider yet.
- [x] LOLP/margin (`system_forecast_api.py`, LOLPDRM) -- confirmed
      directly from source, and the pattern assumed here turned out
      wrong: no `history?publishTime=` mechanism at all. One call
      returns five forecast horizons at once (1h/2h/4h/8h/12h+) --
      genuinely different from demand forecast's shape, not a variant
      of it. `ElexonClient.loss_of_load_forecast()` implemented as a
      plain method, deliberately not forced into `ForecastProvider`.
      See `docs/adr/0010`. Wired into `notebooks/demand_forecast_vs_system_tightness.py`
      as an additive second section -- LOLP compared side by side with
      demand, not a replacement notebook. Field-name-for-which-horizon
      still unconfirmed; that's what the notebook's value-field
      dropdown is for.
- [ ] Generation, surplus, and indicated-generation forecasts
      (`generation_forecast_api.py`, `surplus_forecast_api.py`,
      `indicated_forecast_api.py`) -- given LOLP's pattern turned out
      to differ from demand forecast's, don't assume any of these
      three match either. Read each source file before writing a
      client method, not before this time.
- [ ] Real energy and DC prices wired into `core` (`ElexonPriceProvider`,
      `NesoPriceProvider`) -- carried over from the original v1 scope.
- [ ] Real, calibrated pricing for `bm_offer`/`bm_bid` -- the registry
      entries themselves are already built (Phase A, `docs/adr/0008`),
      directional and structurally correct (`bm_offer` discharge,
      `bm_bid` charge, mirroring DC-High/DC-Low), but still priced by
      `SyntheticPriceProvider` placeholders. BM prices/volumes
      genuinely cannot be forecast day-ahead -- what's proposed
      instead is a calibrated *tightness proxy*, not a real forecast:
      - **Correction, from real data (`docs/adr/0012`):** LOLP/margin
        was the planned signal, on the reasoning that combining
        demand and generation should beat demand alone. Tested against
        real winter and summer weeks: LOLP sits at ~0 across every
        horizon (correct behaviour -- it measures rare capacity-
        adequacy risk, not routine balancing noise) and de-rated
        margin shows no relationship to NIV direction (it answers a
        different, coarser, slower-moving question than NIV does).
        LOLP is not the signal. Wind was chosen as the next candidate
        (`docs/adr/0013`) over falling back to demand-alone, since
        wind forecast error is the actual dominant driver of the
        short-term balancing noise that margin turned out not to
        explain.
      - Whichever signal: calibrate against real historical outturn,
        the same shape regardless -- bucket by the chosen variable,
        and for each bucket look at the realised probability and price
        distribution of `classify_system_length()` (already built,
        already validated against Elexon's own published SPAR
        figures) -- Short periods historically command higher System
        Prices than Long ones, which is exactly the asymmetry
        `bm_offer` vs `bm_bid` needs to reflect.
      - This is an empirical relationship, not a formula to guess at
        -- explore and validate it in a notebook (same "explore, then
        promote" workflow as everything else in `ingestion/`) before
        any of it reaches the live optimizer. Already proven valuable
        once: this is exactly the discipline that caught LOLP being
        the wrong signal before any code depended on it.
      - Real limit, stated plainly: this improves *how much capacity
        the day-ahead plan reserves* for probable BM upside -- it does
        not make BM day-ahead-schedulable in the literal sense. Actual
        BM bid execution is real-time and stays a separate, later
        concern (see the open question in Phase C above).
      - `delivery_hours` default: one settlement period (0.5h), same
        granularity as wholesale -- a simplification, worth revisiting
        once real acceptance-duration data (already fetchable via
        `bid_offer_acceptances`) is actually examined.
      - [x] Demand forecast validated as a starting single-variable
        proxy: three real 7-day windows (winter/spring/summer) show
        higher forecast demand genuinely correlates with more Short
        periods, but imperfectly -- expected, since demand is only
        half of the balance that determines tightness (wind
        generation forecast error is the other half, and demand
        forecast says nothing about it).
      - [x] LOLP/margin added as a second interpretable variable and
        tested against real data (winter + summer) -- **result:
        rejected as a signal for this purpose**, not inconclusive. See
        `docs/adr/0012` for why LOLP measuring capacity adequacy
        rather than routine balancing noise makes this the correct,
        expected outcome rather than a failed experiment. The
        client method and notebook section stay -- they're right for
        a genuinely different question (capacity-stress analysis),
        just not this one.
      - [x] Wind added as a third variable, framed correctly around
        **volatility** (price dispersion, `spread_by_bin()`/
        `spread_chart()`) rather than reusing the direction-counting
        approach from demand/LOLP -- volatility and direction are
        different questions, see `docs/adr/0013`. Wind genuinely fits
        `ForecastProvider` (confirmed real `/history` endpoint,
        unlike LOLP) -- `ElexonWindForecastProvider` is the second
        real provider implementation. **Not yet done:** the mechanism
        is proven against mocked data (a deliberately shaped low-
        vs-high-spread test), but whether wind forecast actually
        predicts volatility in reality still needs the notebook run
        against real data, the same way LOLP's rejection needed real
        winter/summer weeks, not just a working pipeline. The
        `publishTime` query parameter name on wind's history endpoint
        is inferred from convention, not independently confirmed the
        way demand forecast's was -- a real, named risk, not a silent
        one (`verify_wind_forecast_schema()` exists to catch it).
      - [ ] Full ML model (many forecast features, a trained
        regressor) explicitly deferred, not rejected -- revisit once
        Phase D's backtest exists to judge a model by realized
        decision quality, not prediction accuracy in isolation.
        Interpretability is a real cost of this path, not just a
        preference: it's what makes a wrong calibration debuggable
        instead of a second black box replacing the commercial ones
        this project exists as an alternative to.
- [ ] Expected-value derating for auction/acceptance risk -- BM-Offer,
      BM-Bid, DC-High, and DC-Low all clear through competitive
      auctions; committing capacity to a market and *not* getting
      accepted is a real, priced risk, not an edge case (the "skip
      rate" concept flagged in the very first research done for this
      project). Same shape as the BM tightness proxy above, applied to
      a different question:
      - Optimizer objective changes from `reserve x dt x price` to
        `reserve x dt x price x P(accepted)` -- a change to the price
        *input*, not the LP's structure.
      - `P(accepted)` calibrated from real historical acceptance data,
        already fetchable: `bid_offer_acceptances_for_day()` for BM,
        the confirmed NESO EAC resource IDs (`neso.py`) for DC-High/
        DC-Low.
      - Same sequencing discipline as everything else here: notebook
        first, real data, validated before it reaches `optimizer.py`.

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
- `docs/adr/0009` -- ForecastProvider lives in ingestion; Elexon
  demand forecast is the first implementation (Phase B, in progress)
- `docs/adr/0010` -- LOLP/margin doesn't fit ForecastProvider's shape
  (five horizons per call, no vintage mechanism) -- stayed a plain
  client method; wired into the demand-forecast notebook as a second,
  additive section (Phase B, in progress)
- `docs/adr/0011` -- query parameters go through `requests`' `params=`,
  not hand-built URL strings -- found live (a timezone offset's `+`
  meant "space" when sent unescaped), not by anything in this test
  suite; fixed in all five affected methods, not just the one
  reported. A real, named limitation of mocking `requests.get`
  entirely: these tests prove intent, not wire-level correctness.
- `docs/adr/0012` -- LOLPDRM doesn't predict short-term NIV direction,
  tested against real winter and summer data -- a genuine correction
  to the BM-proxy plan, not a failed experiment. LOLP measures rare
  capacity-adequacy risk; NIV reflects routine, every-period balancing
  noise. Different questions, different timescales.
- `docs/adr/0013` -- wind forecast added as a volatility signal
  (`spread_by_bin`/`spread_chart`), deliberately not the direction-
  counting approach reused from demand/LOLP -- volatility and
  direction are different questions. Wind genuinely fits
  `ForecastProvider`, unlike LOLP; the mechanism is proven against
  mocked data, the real-data finding isn't in yet.
