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

**Pivot, 2026-08-08: fast path to a real v1, not more signal research
first.** Phase B's exploratory work (demand, LOLP, wind, DC
requirements, inertia, largest loss) produced real, valuable findings
-- but none of it has reached `optimizer.py` yet, and continuing to
chase signal quality delays ever having a working app. Decision: ship
a core day-ahead stacked optimizer across DC, BM, and wholesale now,
with each market's data honestly labelled by how real it actually is,
rather than waiting for every signal to be fully calibrated first.

- **Wholesale [x] implemented, corrected four times, each before
  causing real damage (`docs/adr/0017`):** (1) MID is realised/settled
  data, not a forecast, and the trigger runs *before* N2EX's 09:50
  gate closure, when tomorrow's price genuinely doesn't exist as
  settled fact yet -- caught before any code was written. (2) The
  initial guess at MID's endpoint and parameters
  (`/balancing/pricing/market-index`, `settlementDate`/
  `settlementPeriod`, a considered inference from every other
  confirmed opinionated endpoint in this project) was wrong; the real
  shape, confirmed against a live request URL, is `/datasets/MID`
  with `from`/`to`/`dataProviders`. (3) MID also only allows a 7-day
  span per request, also confirmed live --
  `market_index_data_range()` chunks any longer window automatically;
  `ElexonWholesalePriceProvider`'s default 28-day lookback now costs
  4 chunked requests. (4) N2EXMIDP (the default data provider,
  originally picked on general "N2EX is the more liquid GB exchange"
  market knowledge) showed zero price and volume for the most recent
  several days live, while APXMIDP for the identical dates showed real
  data -- ruling out a general reporting lag, since that would affect
  both providers equally. Default changed to APXMIDP throughout.
  `wholesale_prices()` uses MID as historical training data: a
  seasonal average by settlement period, never including the target
  day itself (proven by test, not just documented), with real, sourced
  zero-value exclusion (kept regardless of provider, in case this
  recurs or a different provider has its own gaps later). A genuinely
  predictive model (demand/wind forecasts as explanatory inputs) stays
  deferred, same reasoning as `docs/adr/0012`. Implements only
  `wholesale_prices()`, not the full `PriceProvider` protocol -- using
  it in a stacked (DC/BM-active) optimizer run will raise
  `AttributeError` at `reserve_prices()`, a deliberate visible failure,
  not a silent wrong answer.
  **Still genuinely open:** field names inside a MID row are
  unconfirmed -- only the endpoint, query parameters, and now the
  working data provider have been checked live.
  **Checked and deliberately not pursued:** Nord Pool and EPEX SPOT
  (the actual day-ahead exchanges) have no free, publicly accessible
  API for historical auction data -- confirmed from multiple
  independent sources, including a project built specifically to work
  around that gap. Real access requires a paid EPEX SPOT/EEX Group
  subscription or a paid third-party aggregator (Modo Energy, Montel).
  Modo Energy specifically is one of the commercial platforms this
  whole project has been positioned as a transparent, free alternative
  to -- using their feed would be a deliberate departure from that
  principle, not a quiet substitution, and hasn't been made.
- **DC [x] implemented, one correction confirmed live
  (`docs/adr/0018`):** real historical clearing prices
  (`response_reserve_results_summary`, already confirmed) --
  `NesoDCPriceProvider.reserve_prices()`, a simple EFA-block average
  for v1 pricing, not the inertia/loss-calibrated model from Phase B's
  exploratory work. That sophistication is a real future upgrade,
  deliberately deferred, not abandoned. Two real gaps closed along the
  way: `NesoClient.datastore_search()` had no `sort` parameter (added,
  backward compatible -- without it, a plain `limit=N` fetch against
  nearly three years of data has no guaranteed relationship to
  recency), and DC's per-EFA-block granularity needed broadcasting
  each block's average across the 8 settlement periods it covers, via
  a new `efa_block_number_for_hour()` (proven to genuinely be the
  inverse of the existing `efa_block_label_for_index()`). Covers only
  `reserve_prices()` for DC-High/DC-Low -- raises for any other
  market. **Correction, confirmed live:** the field distinguishing
  DC-High/DC-Low is `auctionProduct` (`DCH`/`DCL`), not `serviceType`
  as first guessed -- `serviceType` is real but holds an unrelated
  category ("Response", "Slow Reserve"); the original guess would have
  matched nothing. The now-unused `service_type_field` parameter was
  removed rather than left in place doing nothing.
- **BM [x] implemented (`docs/adr/0019`):** BM is genuinely harder than
  wholesale or DC -- pay-as-bid, not pay-as-clear, so there's no
  single "the BM price" to average toward, unlike MID or DC's
  clearing price. `MarketSpec`'s own docstring already states BM's
  price needs to be "an expected-value proxy... already probability-
  weighted." `ElexonBMPriceProvider.reserve_prices()` averages
  *submitted* price levels (BOD, confirmed price-bearing per ADR
  0006 -- BOALF carries volume/timing, not price) across all BM
  units, multiplied by a stated, conservative, uncalibrated
  `acceptance_derating` (default 0.3) -- honest about being an
  approximation of an approximation, not dressed up as more than it
  is. Real acceptance-rate calibration (BOD+BOALF joined) remains
  the deliberately-deferred acceptance-risk work already on this
  roadmap. No `verify_schema()`-equivalent built for BOD specifically
  yet -- field name defaults (`offerPrice`, `bidPrice`) are unchecked
  guesses, an honest gap, not silently assumed correct.
- **`CombinedPriceProvider` redesigned** to route across multiple
  reserve providers (`reserve_providers`, plural -- a breaking change
  from its first version, made before any real caller depended on the
  old shape): tries each provider in turn, catching the `ValueError`
  each one already raises for markets it doesn't cover. Composes
  wholesale + DC + BM into one object satisfying core's full
  `PriceProvider` protocol -- ready for a real three-market stacked
  test run, the actual target this whole fast-path plan was aimed at.
- **Confirmed directly from `optimizer.py`/`markets.py`'s own source,
  not memory, while answering a question about the registry:**
  `Market.WHOLESALE` is deliberately absent from `MARKET_REGISTRY` --
  the registry only ever held reserve-style capacity commitments (a
  direction, a delivery duration); wholesale is the underlying
  charge/discharge decision the battery always has available, not a
  capacity reservation. `optimize_day()` treats them as genuinely
  different: `active_reserve = [m for m in markets if m in
  MARKET_REGISTRY]` (registry-driven) vs `wholesale_active =
  Market.WHOLESALE in markets` (a direct enum check). Not a gap in
  the registry -- the intended design, already stated in
  `markets.py`'s own module docstring.
- **Imperfect price capture / acceptance risk**: folded into one
  mechanism for v1, not a research program -- a single configurable
  derating parameter applied to DC and BM reserve revenue
  (`expected_value = reserve x dt x price x derating_factor`), same
  mechanical shape as the acceptance-risk design already on this
  roadmap, with a placeholder constant instead of a calibrated
  function. States the limitation exists rather than hiding it; does
  not solve it.
- The optimizer itself needs no changes for any of this --
  `MARKET_REGISTRY` and the LP structure (Phase A) already handle this
  shape. This is real `PriceProvider` wiring plus one derating
  parameter, not new optimization logic.

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
      instead is a calibrated *tightness proxy*, not a real forecast.
      **This item split into two independent threads
      (`docs/adr/0014`), not one blocked on the other:**

      **Thread 1 -- direction/bias calibration (still open).** Which
      way to bias `bm_offer` vs `bm_bid` pricing. No working signal
      yet:
      - [x] Demand forecast: three real 7-day windows (winter/spring/
        summer) show higher forecast demand genuinely correlates with
        more Short periods, but imperfectly -- expected, since demand
        is only half of the balance that determines tightness.
      - [x] LOLP/margin: tested against real data (winter + summer)
        and **rejected**, not inconclusive (`docs/adr/0012`) -- LOLP
        measures rare capacity-adequacy risk, not the routine
        balancing noise that drives NIV direction.
      - [x] Wind: tested against real data and **also doesn't predict
        direction** -- see Thread 2 below for what it turned out to
        be good for instead.
      - [ ] No candidate left with a usable direction signal. Next
        step genuinely open -- possibilities include a combination of
        the above, or accepting that day-ahead direction may not be
        reliably predictable at this level and designing around that
        rather than continuing to search for a single-variable proxy.
      - Whichever signal eventually works: calibrate against real
        historical outturn -- bucket by the variable, and for each
        bucket look at the realised probability and price distribution
        of `classify_system_length()` (already built, already
        validated against Elexon's own published SPAR figures) --
        Short periods historically command higher System Prices than
        Long ones, exactly the asymmetry `bm_offer` vs `bm_bid` needs.

      **Thread 2 -- volatility-informed capacity reservation (signal
      validated, mechanism not designed yet).**
      - [x] Wind forecast validated as a real volatility signal: above
        ~4GW forecast wind, actual System Price shows meaningfully
        higher dispersion (`docs/adr/0013`, `docs/adr/0014`). Makes
        sense together with the direction-null result, not despite it
        -- wind forecast error is roughly symmetric, so it should
        predict spread without predicting sign. The 4GW threshold
        itself hasn't been checked beyond one winter+summer sample.
      - [ ] Design need, not yet started: how volatility actually
        translates into how much headroom/footroom the day-ahead plan
        reserves. The reasoning (higher volatility -> higher option
        value in staying flexible, same logic as financial option
        pricing) is recorded in `docs/adr/0014`; the mechanism --
        a formula, a lookup table, something else -- is not designed.
      - `ElexonWindForecastProvider` is the second real
        `ForecastProvider` implementation (after demand) -- wind
        genuinely fits the protocol, unlike LOLP, via a confirmed real
        `/history` endpoint. The `publishTime` query parameter name is
        inferred from convention, not independently confirmed the way
        demand forecast's was -- a real, named risk
        (`verify_wind_forecast_schema()` exists to catch it).

      **Applies to both threads:**
      - This is an empirical relationship, not a formula to guess at
        -- explore and validate in a notebook (same "explore, then
        promote" workflow as everything else in `ingestion/`) before
        any of it reaches the live optimizer. Already proven valuable
        twice: this discipline caught LOLP being the wrong signal, and
        caught wind answering a different question than the one it was
        sought for, both before any code depended on either.
      - Real limit, stated plainly: this improves *how much capacity
        the day-ahead plan reserves* for probable BM upside -- it does
        not make BM day-ahead-schedulable in the literal sense. Actual
        BM bid execution is real-time and stays a separate, later
        concern (see the open question in Phase C above).
      - `delivery_hours` default: one settlement period (0.5h), same
        granularity as wholesale -- a simplification, worth revisiting
        once real acceptance-duration data (already fetchable via
        `bid_offer_acceptances`) is actually examined.
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
      - [x] DC requirement volumes, the first concrete input for this
        thread: `notebooks/dc_requirements_by_efa_block.py`
        (`docs/adr/0015`). DC is pay-as-clear, so requirement volume
        is naturally a `P(accepted)` signal, not a price signal --
        higher requirement means more capacity clears, so a given bid
        is more likely to be among the accepted ones. Confirmed
        directly from NESO's own page that DC requirements are driven
        by demand, inertia, response volumes, and largest losses (not
        three inputs, four). EFA blocks confirmed via a real NESO
        market report (6 blocks, 4 hours each, starting 23:00). A
        genuinely new access pattern needed: this dataset is CSV-
        download-only, not `datastore_search`-active, the first
        method in `neso.py` built that way.
      - [x] **Real finding, from live data:** DC requirement drops as
        inertia increases -- DC-Low between roughly 1000-1200MW,
        DC-High 1200-1400MW, DC-High consistently above DC-Low. Makes
        sense given the actual regulatory asymmetry: DC-High must
        contain frequency to a 50.5Hz ceiling (only 0.5Hz of
        headroom), while DC-Low can extend to 49.2Hz provided it
        recovers to 49.5Hz within 60 seconds -- a tighter high-side
        limit requiring proportionally more secured capacity. This is
        a real, load-bearing finding now underpinning the trading
        app's DC modelling, not a hypothesis still being checked.
      - [x] Largest secured loss, reconstructed and joined against the
        same inertia data (`docs/adr/0016`) to check whether it moves
        DC requirement *independent* of inertia, not just alongside
        it. No "largest loss" dataset exists anywhere -- confirmed by
        search, then reconstructed from real per-BMU metered output
        (B1610, `/datasets/B1610`, a third distinct Elexon endpoint
        family) for SIZB and interconnectors, identified via the same
        ID-pattern matching already proven for battery identification.
        Import/export direction inferred from B1610's value sign --
        **genuinely unconfirmed for interconnectors specifically**,
        stated plainly, not glossed over. The independence check
        itself is a median-tercile stratification (each comparison
        built unconditional, then again restricted to the middle
        third of the inertia range) rather than a regression --
        simpler, and its reasoning stays fully inspectable, consistent
        with this project's standing preference (`docs/adr/0012`).
        **Not yet done:** the mechanism is proven against realistic
        mocked data (16 tests, including one proving an irrelevant BMU
        with larger output than either real candidate never leaks into
        the loss figure) -- whether largest loss actually holds up
        independent of inertia, and whether the import/export sign
        convention is even correct, both need this run against real
        data before either goes near `optimizer.py`.
      - **Correction, confirmed live the same day:** B1610's real
        query parameters are `settlementDate` + `settlementPeriod`,
        not the `from`/`to` bulk-range shape first assumed (sourced,
        wrongly, from a third-party wrapper's convenience parameter
        names rather than Elexon's own API documentation -- exactly
        the risk ADR 0011 already named). A full day now costs 48
        requests, not one -- the notebook's B1610 fetch has an
        explicit days-to-fetch slider and live cost readout as a
        result, replacing what had been an unbounded fetch across
        whatever range the DC requirements data happened to span.
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
- `docs/adr/0014` -- wind forecast validated against real data as a
  volatility signal (higher dispersion above ~4GW forecast), with no
  direction relationship -- the first positive result in this line of
  work. Splits the BM-proxy plan into two independent threads:
  direction/bias calibration (still unresolved) and volatility-
  informed capacity reservation (signal validated, mechanism not yet
  designed).
- `docs/adr/0015` -- DC requirement volumes by EFA block, the first
  concrete input for the acceptance-risk thread. New CSV-download
  access pattern for NESO (confirmed not datastore-active), EFA
  blocks confirmed via a real NESO market report, real schema
  correction after live testing (long-by-service, wide-by-EFA-column,
  not the flat shape first assumed). Inertia tested as
  outturn-vs-outturn, not a vintage-matched forecast -- and, from real
  data, DC requirement genuinely drops as inertia rises, DC-High
  consistently above DC-Low, matching the real regulatory frequency-
  containment asymmetry (50.5Hz ceiling vs 49.2Hz/60s-recovery floor).
- `docs/adr/0016` -- largest secured loss, reconstructed from real
  per-BMU output (B1610) since no NESO dataset publishes it directly.
  A third distinct Elexon endpoint family (`/datasets/{code}`).
  Independence from inertia checked via median-tercile stratification,
  not a regression -- simple and fully inspectable. Interconnector
  import/export sign convention remains genuinely unconfirmed.
  Corrected the same day, confirmed live: real params are
  `settlementDate`/`settlementPeriod` (48 requests/day), not the
  `from`/`to` bulk-range shape first assumed from a third-party
  wrapper's own parameter names rather than Elexon's API docs.
- `docs/adr/0017` -- wholesale price via seasonal average of MID
  (Market Index Data), not live MID -- corrected before any code was
  written once it was clear the trigger runs before N2EX's 09:50 gate
  closure, when tomorrow's price genuinely isn't settled yet.
  Corrected again, same day, confirmed live: the real endpoint is
  `/datasets/MID` with `from`/`to`/`dataProviders`, not the
  `settlementDate`/`settlementPeriod` opinionated-endpoint pattern
  first guessed. Corrected a third time, same day: MID also only
  allows a 7-day span per request -- `market_index_data_range()`
  chunks longer windows automatically. Corrected a fourth time, same
  day: N2EXMIDP (the original default, picked on general market
  knowledge) showed zero data live while APXMIDP showed real data for
  the same dates -- default changed to APXMIDP. Field names inside a
  MID row remain the one thing still unconfirmed. Also confirmed:
  Nord Pool and EPEX SPOT have no free public API for day-ahead data
  -- checked and deliberately not pursued, not silently worked around.
  First real `PriceProvider`-compatible implementation in this project
  (`ingestion/wattstack_ingestion/prices.py`); implements only
  `wholesale_prices()` so far.
- `docs/adr/0018` -- DC price via seasonal average by EFA block,
  mirroring wholesale's approach. `NesoDCPriceProvider.reserve_prices()`
  for DC-High/DC-Low only, dispatching on `market.name` rather than
  importing `core.markets.Market` (ADR 0009's rule). Two real gaps
  closed before building it: `datastore_search()` gained a `sort`
  parameter (without it, "most recent limit rows" isn't a safe
  assumption against nearly three years of data), and a new
  `efa_block_number_for_hour()` broadcasts each EFA block's average
  across its 8 settlement periods. Unlike wholesale, not yet checked
  against a real response -- field names are reasoned guesses,
  correctable via constructor parameters.
- `docs/adr/0019` -- BM price via a derated seasonal average of
  submitted BOD price levels, the last of the three fast-path
  providers. Genuinely harder than wholesale/DC (pay-as-bid, no
  single clearing price) -- `MarketSpec` already states BM's price
  needs to be an expected-value proxy, and `acceptance_derating`
  (0.3, stated as uncalibrated) is that proxy's mechanism.
  `CombinedPriceProvider` redesigned to route across multiple reserve
  providers by market, a breaking change made deliberately before any
  real caller depended on the old singular-provider shape.
