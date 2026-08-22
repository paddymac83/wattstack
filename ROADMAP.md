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

**v2 pivot, 2026-08-09: from seasonal averages to real predictive
models, starting with BM/imbalance.** All three fast-path providers
(wholesale, DC, BM) are seasonal averages, not forecasts -- honest
baselines, deliberately simple. First real predictive-modelling
effort: `docs/adr/0020`, built from two real sources (Timera Energy's
"the risk is in the distribution, not the mean"; Browell & Gilbert's
Energies 2022 paper on imbalance price forecasting), targeting System
Price directly rather than `ElexonBMPriceProvider`'s BOD average.
`notebooks/imbalance_price_probabilistic_forecast.py` implements a
demand-probability-weighted mixture of conditional price
distributions, validated with a genuine chronological train/test
split and a real backtest against the flat baseline. Honest
expectation, stated directly from the paper's own result: day-ahead
imbalance forecasting is hard even in the published literature (3%
MAE improvement over climatology), so a modest, measurable improvement
is the real target, not a large leap.

**Real negative result, confirmed live, same day:** the first version,
run against real GB data, gave a mixture MAE of £22.22/MWh against a
£5.67/MWh flat baseline -- nearly 4x worse, not a modest miss.
Diagnosed, not dismissed: raw empirical probabilities from thin demand
buckets (a handful of observations showing an extreme rate by chance)
made the mixture model confidently wrong, which costs more than being
honestly uncertain. Fixed with `shrink_probability_by_bin()` (new) --
pulls thin-bucket estimates toward the overall rate; proven on
realistic noisy synthetic data to correctly convert an actively
harmful result (-9.5%) into an essentially neutral one (-0.7%) when
the underlying signal is genuinely weak. A shrinkage-strength sweep
was added to the notebook (`[0, 1, 2, 5, 10, 20, 50, 100]` tried
automatically against the same backtest), so finding a workable value
against real data takes one run instead of manually adjusting a
slider repeatedly. **Not yet re-run against the same real data that
produced the original bad result** -- that's still the immediate next
step, not this fix's own validation, which has only used synthetic
data so far. Wind's already-validated volatility signal still isn't
used in this architecture at all -- a real, named gap, separate from
the shrinkage fix.

**Two real test-methodology bugs found while building the sweep's own
tests, both worth carrying forward as standing lessons:** (1) a test
built on `hash(day)` for pseudo-random-looking mock data was silently
non-deterministic -- Python randomises string hash values per process
by default, so the test's pass/fail depended on which random seed
happened to be active, not on the logic being tested. Fixed with
`date.toordinal()`, confirmed stable across repeated runs with fresh
random seeds. (2) even once deterministic, the test's own claim was
wrong -- strict monotonic improvement from more shrinkage isn't
something shrinkage actually guarantees on any single backtest; what's
genuinely guaranteed is convergence to the unconditional rate as
strength grows large, now tested directly against the function itself
instead of asserted incorrectly through a notebook-level backtest.

**Promoted to production, `docs/adr/0021`, correcting the shrinkage
diagnosis rather than confirming it:** re-run with a realistic 60-day
training window (not the original 1-2 days) and a 12-day chronological
holdout, the unshrunk mixture model achieved a real **2.3% MAE
improvement** over the flat baseline -- close to Browell & Gilbert's
own published 3% day-ahead result, a genuine match to peer-reviewed
literature on real GB data. The actual root cause of the original bad
result was too little training data, not overconfidence from thin
buckets -- shrinkage was a real fix for a real (demonstrated)
failure mode, just not the one that actually occurred here.
`ElexonImbalancePriceProvider` (`prices.py`) targets `BM_OFFER`/
`BM_BID` directly, no shrinkage applied, `ElexonBMPriceProvider`
(BOD-average) kept in the module as a known alternative but no longer
the recommended default. First genuinely predictive (not
seasonal-average) price provider reaching `optimizer.py` in this
project.

**Asymmetric BM_OFFER/BM_BID pricing, `docs/adr/0022`:** the two
markets no longer receive the same blended forecast for a given
period. `BM_OFFER` (discharge, valuable when Short) now gets
`P(Short) x mean_price_given_short`; `BM_BID` (charge, valuable when
Long) gets `P(Long) x mean_price_given_long` -- an implicit ~0
contribution from each market's "wrong" regime, a real economic
modelling choice rather than a statistical convenience, stated as such
and open to revision if real acceptance data later supports partial
off-regime value. The fallback path was upgraded alongside the main
one: a period with missing target-day demand now falls back to the
dataset's overall Short/Long rate through the same per-market formula,
not a flat blend that would have silently reintroduced the problem
this change removes.

**BM SoC modelling and acceptance-risk pricing, `docs/adr/0023`.** A
real gap, visible directly in a live run: every reserve market had
zero effect on SoC beyond headroom/footroom margin -- BM-Offer sat at
full power for 42 consecutive periods with SoC flat throughout, which
is not how BM actually works once a bid is called. Fixed with an
optional `acceptance_probability(day, market) -> list[float]`
`PriceProvider` extension (checked via `hasattr()`, not added to the
formal Protocol, so `SyntheticPriceProvider` and every provider
without an acceptance-risk concept are unaffected), feeding an
expected-energy term into the SoC recursion for BM specifically (never
DC, which is genuinely availability-only) -- proven correct by an
isolated-period hand-calculation test and a full-recursion
reconstruction test, not just that the solver still finds a solution.
Headroom/footroom stay based on the *full*, un-derated commitment --
a low acceptance probability must never justify holding less margin
than the worst case would need. A real, sourced fact grounds the
30-minute duration already in `MARKET_REGISTRY`: NESO's MEL/MIL rule
change (11 March 2024, Modo Energy's reporting) moved battery declared
availability from 15 to 30 minutes specifically because most real BM
actions run that long -- confirming, not just assuming, that
`delivery_hours=0.5` for BM was already correct.
`ElexonImbalancePriceProvider` gains `derating_factor` (0.3, same
starting point `ElexonBMPriceProvider` used, still uncalibrated) --
deliberately the *same* number driving both the price discount and the
SoC impact, not two independently-tunable values that could drift
apart. The ADR's own "future work" section engages directly with the
deeper point raised alongside this request: acceptance risk applies to
all four markets, not just BM (DC has zero risk-modelling for auction
clearing itself); a flat constant conflates "was the regime forecast
right" with "was *my* specific bid the one selected"; a real
calibration path exists (BOD+BOALF joined) and isn't built; and the
deepest version of this isn't a better price signal at all but a
genuinely different optimizer structure (two-stage stochastic, or a
VaR/CVaR-constrained approach using the notebook's already-computed,
currently-unused conditional quantiles) -- named as real, substantial
future work, not implied solved by one derating parameter.

**Strategy pivot, discussed then built: BM/intraday descoped from
the primary optimization**, treated as a fallback layer after DC and
day-ahead wholesale decisions, not actively optimized for. Real
timeline facts anchor the resulting architecture, confirmed directly
from NESO's own current guidance rather than assumed: wholesale gate
closure 09:50, DC/DM/DR/BR/QR gate closure 14:00 (an older document
showed 14:30, superseded once EAC consolidated these services into one
auction). Since DC's deadline is *after* wholesale's, and wholesale
results are known well before 14:00, the right architecture is a
genuine two-stage decision -- wholesale committed first under a
probabilistic view of DC's value, DC committed second with the
wholesale outcome already known -- not a simultaneous joint decision
guessing at both. Built, `docs/adr/0025`, immediately after the DC
SoE work below.

**Two-stage optimizer, `docs/adr/0025`.** `optimize_day()` gains
`fixed_wholesale_mw` -- a schedule already decided elsewhere, not a
free decision for that call, validated (length, physical bounds)
before any LP construction. No duplicated LP logic needed: PuLP
accepts plain floats interchangeably with `LpVariable`s, so the same
constraint/objective code handles both the free and fixed case.
`optimize_day_two_stage()` runs the existing joint LP for stage 1
(wholesale + DC, informed by an expected view of DC's value -- not
blind to it), keeps only its wholesale schedule, then re-runs DC alone
for stage 2 with that schedule fixed and (optionally different)
stage-2 pricing. `TwoStageResult`'s field names
(`stage1_plan`/`stage2_final`) are deliberate, not generic -- stage
1's own DC numbers are a discarded planning estimate, and the naming
makes misusing them as the real answer harder. A real infeasibility
bug was found and fixed during testing, not a hypothetical: feeding
stage 1's rounded output back into stage 2 accumulated a
thousandths-of-an-MWh SoC drift that violated zero-tolerance hard LP
bounds -- fixed with a small, deliberately targeted tolerance applied
only to the fixed-schedule path, diagnosed by hand-recomputing the SoC
trajectory rather than guessed at.

**Strategy pivot: DC-only bidding, wholesale as pure opportunity-cost
proxy, `docs/adr/0026`.** No active wholesale or BM decisions --
capacity committed entirely to DC-L/DC-H at 14:00, priced against what
wholesale (using only pre-09:50 information) and BM would have
forgone. `dc_bid_floor_price()` adds the missing baseline opportunity-
cost term `dc_activation_risk_premium()` didn't cover on its own (that
function only prices the *additional* cost specific to the post-
activation recovery window) -- wholesale price spread across the EFA
block, divided by EFA block hours, same spread-based methodology as
the activation premium for consistency. A genuine timing ambiguity
surfaced while designing the requested DC SoC optimization -- an
8-period vs 7-period total recovery window, both defensible readings
of NESO's own worked example -- was confirmed live and resolved: 8 is
correct, the idle assessment/submission period is a real, separate
delay from the 1-hour gate. See `docs/adr/0027` immediately below for
the correction and the resulting SoC model, built once the timing was
settled rather than guessed at.

**DC multi-period SoC modelling, `docs/adr/0027`.** With the timing
confirmed, the requested DC SoC optimization is built: a new optional
`dc_activation_probability()` `PriceProvider` extension (structurally
identical to BM's `acceptance_probability()`, deliberately a different
method name since it measures a different thing -- probability of a
real activation *event*, not of a bid being selected) feeds a
multi-period expected-SoC term into `core`'s SoC recursion. An
activation at period `t` drains (DC-Low) or charges (DC-High)
immediately, contributes nothing for the confirmed 3 idle/gate periods,
then recovers over exactly the next 5 periods -- proven directly by
test that recovery begins precisely at offset 4 (not 1, 2, or 3), and
that the 5 recovery increments sum to exactly the original drain, not
a partial or over-corrected one. DC-High confirmed as the exact sign-
mirror of DC-Low. Headroom/footroom stay fully un-derated, the same
discipline BM's version already established. All 47 pre-existing core
tests passed unchanged, both before and after -- the new mechanism's
safe default (`0.0` activation probability) preserves every existing
assumption exactly. `dc_activation_probability` itself remains
genuinely unresolved -- no attempt made here to derive it from
anything real.

**DC State-of-Energy management, `docs/adr/0024`.** Every SoE figure
the user described (15-minute minimum energy requirement, 20%-per-SP
recovery, the 1-hour gate, 5%-per-minute ramp rate) confirmed exactly
against NESO's own Dynamic Containment Guidance Document, fetched and
verified directly. A clean algebraic finding: full recovery from empty
always takes exactly 5 settlement periods regardless of contracted MW,
since the minimum requirement and recovery rate scale identically --
plus the confirmed 1-hour gate gives a 7-period (3.5-hour) total
recovery window from a full-depletion event. `dc_activation_risk_premium()`
prices this as an expected-value addition to a DC floor bid (£/MW/h)
-- degradation cost plus foregone wholesale opportunity during the
mandatory recovery window (approximated as price spread across that
window, a deliberately conservative proxy, not a calibration) -- both
scaled by the confirmed minimum energy requirement, weighted by an
`activation_probability` that is explicitly unresolved here, same
treatment as `derating_factor` elsewhere. Deliberately a pricing input,
not a new LP constraint -- an exact, conditional recovery constraint
needs genuinely stochastic, path-dependent state a deterministic-
equivalent day-ahead LP can't represent, the same two-stage/stochastic
territory already named in ADR 0023, not attempted here. A separate,
real discrepancy surfaced and was named, not fixed: `MARKET_REGISTRY`'s
existing `delivery_hours=0.5` for DC assumes a fuller 30-minute
sustained commitment than NESO's actual 15-minute minimum requires --
worth a deliberate decision, not silently altered.

**Wiring `dc_bid_floor_price` into an actual reachable calculation,
retiring the two-stage script, `docs/adr/0028`.** Two real gaps found
directly from trying to use this as built: `dc_bid_floor_price()` and
`dc_activation_risk_premium()` had no caller at all -- undebuggable
from any real script. And `optimize_day_two_stage()` was the wrong
tool entirely for the DC-only strategy (ADR 0026) -- that function
exists to commit a wholesale schedule in stage 1, but the current
strategy never holds a wholesale position, so there's no stage 1
decision to make. `dc_bid_floor_prices_by_efa_block()` (new) is the
actual wiring: given a full day's wholesale forecast, groups periods
into their EFA block (reusing `efa_block_number_for_hour()`, not
reimplemented) and calls the already-tested per-block formula once per
block -- proven by test that each result is identical to calling it
directly, not a parallel calculation that could drift. `dc_floor_price_calculator.py`
replaces `two_stage_wholesale_dc.py` entirely (not alongside it) --
fetches a pre-09:50 wholesale forecast, prints all 6 blocks' floor
prices; at the time of this ADR, a pure calculation with no `core`
import, extended the same day (see `docs/adr/0029` below) once
`dc_activation_probability()` existed to make a dispatch-planning step
meaningful. `ACTIVATION_PROBABILITY` remains the same real, unresolved
number named since ADR 0024 -- now visible and adjustable in one place
in a real script, not still just a buried function parameter.

**Closing the loop: `dc_activation_probability` wired to a real
provider, `docs/adr/0029`.** The `dc_activation_probability()`
`PriceProvider` extension built in ADR 0027 had no real implementation
-- pointed out directly: it couldn't be debugged as part of an actual
end-to-end script. `NesoDCPriceProvider` now implements it (a single
flat, constructor-supplied constant, default `0.02`, same stated-
parameter honesty as `ElexonBMPriceProvider.acceptance_derating`).
`dc_floor_price_calculator.py` gained a Step 2: DC-only dispatch
planning via `optimize_day()`, with wholesale fixed at *exactly* zero
(`fixed_wholesale_mw=([0.0]*48, [0.0]*48)`) rather than merely omitted
from `markets` -- omitting it alone would still leave `charge`/
`discharge` as free variables the solver could use if ever needed for
feasibility against the DC activation/recovery mechanism, silently
reintroducing a wholesale position this strategy explicitly rules out.
A small `_NoWholesaleActivity` adapter closes a real, generalisable gap
found along the way: `fixed_wholesale_mw` triggers a `wholesale_prices()`
fetch for revenue reporting regardless of whether the schedule is all
zero, so any provider paired with it needs at least a trivial
implementation, even one with no wholesale concept at all. Validated
against the actual script file end-to-end with mocked network calls,
not a reimplementation of its logic -- confirmed charge/discharge
exactly zero throughout, `dc_activation_probability` genuinely
readable from the returned `DispatchResult`, and a real, small,
plausible SoC drift proving the mechanism is live. Two real process
lessons recorded rather than corrected quietly: a search-string typo
("price" vs "prices") nearly caused `dc_bid_floor_prices_by_efa_block()`
to be reported as missing when it already existed and was tested; and
a new, duplicate script was written before checking whether
`dc_floor_price_calculator.py` already existed -- it did, and was
extended in place instead.

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
- `docs/adr/0020` -- probabilistic imbalance price forecast, the first
  genuine predictive model in this project (vs. the seasonal averages
  in 0017-0019). Built from two real sources (Timera Energy, Browell
  & Gilbert 2022), targets System Price directly rather than BOD.
  Demand-probability-weighted mixture of conditional price
  distributions, validated with a chronological train/test split.
  Real result against live data: mixture MAE £22.22/MWh vs £5.67/MWh
  flat baseline, a genuine failure. Working diagnosis at the time
  (raw probabilities from thin demand buckets producing confident,
  noise-driven errors) led to `shrink_probability_by_bin()`, proven on
  synthetic noisy data -- corrected by `docs/adr/0021` once re-run
  against real data at proper scale: the actual cause was a training
  window of only 1-2 days, not thin-bucket overconfidence. A
  shrinkage sweep remains in the notebook as a useful diagnostic tool
  regardless. Two real test-methodology findings along the way: a
  `hash()`-based mock was silently non-deterministic across process
  runs, and strict monotonic improvement from shrinkage was wrongly
  asserted -- what's actually guaranteed is convergence to the
  unconditional rate as strength grows large.
- `docs/adr/0021` -- `ElexonImbalancePriceProvider` promoted to
  production, correcting ADR 0020's shrinkage diagnosis rather than
  confirming it: with a realistic 60-day training window, the
  *unshrunk* mixture model achieved a real 2.3% MAE improvement over
  the flat baseline -- close to Browell & Gilbert's own published 3%
  day-ahead result. No shrinkage applied in production; the real fix
  was training data volume, not overconfidence correction.
  `ElexonBMPriceProvider` kept as a known alternative, no longer the
  recommended default. First genuinely predictive price provider
  reaching `optimizer.py` in this project.
- `docs/adr/0022` -- asymmetric BM_OFFER/BM_BID pricing. Each market
  now gets `P(its own regime) x mean_price_in_that_regime`
  (`BM_OFFER`: Short; `BM_BID`: Long), replacing the shared blended
  forecast both markets received before -- a real economic modelling
  choice (~0 implicit value in the "wrong" regime), stated as such.
  Fallback path upgraded alongside the main one to preserve the same
  asymmetry rather than reverting to a flat blend.
- `docs/adr/0023` -- BM SoC modelling via an expected-energy term
  (only for energy-settled BM, never availability-style DC), grounded
  in NESO's real 30-minute MEL/MIL rule change; `ElexonImbalancePriceProvider`
  gains `derating_factor` (0.3, uncalibrated), the same number driving
  both the price discount and the SoC impact. Headroom/footroom
  deliberately stay un-derated -- full worst-case margin regardless of
  acceptance likelihood. Extensive "future work" section: acceptance
  risk applies to all four markets, not just BM; a flat constant
  conflates two genuinely different risks; a real BOD+BOALF
  calibration path exists unbuilt; and the deepest fix is a different
  optimizer structure entirely (two-stage stochastic or VaR/CVaR),
  not a better price signal -- named as real future work, not implied
  solved.
- `docs/adr/0024` -- DC State-of-Energy management, every figure
  confirmed exactly against NESO's own Dynamic Containment Guidance
  Document (15-min minimum energy requirement, 20%-per-SP recovery,
  1-hour gate, 5%-per-minute ramp rate). Full recovery from empty is
  always 5 SPs regardless of contracted MW (the requirement and
  recovery rate scale identically); with the gate, a 7-period (3.5h)
  total recovery window. `dc_activation_risk_premium()` prices this
  as a DC floor bid addition (degradation + foregone wholesale
  opportunity during recovery, both scaled by the confirmed minimum
  energy requirement) -- a pricing input, not a new LP constraint;
  the exact stochastic recovery constraint remains named future work.
  A real discrepancy surfaced: `delivery_hours=0.5` assumes more
  sustained-delivery margin than NESO's actual 15-minute requirement.
- `docs/adr/0025` -- two-stage optimizer: `optimize_day()` gains
  `fixed_wholesale_mw` (validated, LP-unified via PuLP's plain-float
  support -- no duplicated logic), `optimize_day_two_stage()` runs
  wholesale+DC jointly for stage 1, keeps only its wholesale schedule,
  re-decides DC alone for stage 2 with that schedule fixed.
  `TwoStageResult.stage1_plan`/`.stage2_final` naming deliberately
  makes misusing the discarded stage-1 DC estimate harder. Found and
  fixed a real infeasibility bug during testing: stage 1's rounded
  output, fed back as stage 2's fixed input, accumulated enough SoC
  drift to violate zero-tolerance hard LP bounds -- fixed with a
  small, targeted tolerance on the fixed-schedule path only.
- `docs/adr/0026` -- `dc_bid_floor_price()`, combining a new wholesale
  opportunity-cost term (EFA-block price spread / EFA hours, same
  methodology as the activation premium) with the existing
  `dc_activation_risk_premium()`. Surfaced a genuine timing ambiguity
  while designing the requested DC SoC modeling: an 8-period vs
  7-period total recovery window, both defensible readings of NESO's
  own worked example -- confirmed live and resolved in `docs/adr/0027`.
- `docs/adr/0027` -- DC multi-period SoC modelling, built once the
  timing was confirmed (8 periods: 1 idle assessment + 2 gate + 5
  recovery, correcting `docs/adr/0024`'s original 7-period undercount).
  New `dc_activation_probability()` `PriceProvider` extension feeds a
  multi-period expected-SoC term into the recursion -- drain/charge
  immediately, nothing for 3 idle/gate periods, recovery over the next
  5, proven exact by test (recovery starts precisely at offset 4;
  the 5 increments sum to exactly the original drain). DC-High
  confirmed the exact sign-mirror of DC-Low. All 47 pre-existing core
  tests unaffected -- the new mechanism's safe default preserves every
  existing assumption exactly.
- `docs/adr/0028` -- `dc_bid_floor_prices_by_efa_block()`, the wiring
  `dc_bid_floor_price()` was missing (previously had no caller,
  undebuggable from any real script). `dc_floor_price_calculator.py`
  replaces `two_stage_wholesale_dc.py` entirely -- the two-stage
  optimizer was the wrong tool for a strategy that never holds a
  wholesale position; at the time of this ADR, a direct calculation,
  no `core` import, no LP solve involved -- extended same-day, see
  `docs/adr/0029`.
- `docs/adr/0029` -- `NesoDCPriceProvider.dc_activation_probability()`
  (a flat, constructor-supplied constant), closing the loop the
  previous ADR left open: `dc_floor_price_calculator.py` gained a
  Step 2, DC-only dispatch planning via `optimize_day()` with
  wholesale fixed to exactly zero, not merely omitted (which would
  leave it usable by the solver for feasibility, silently
  reintroducing a position this strategy rules out). Validated against
  the real script file end-to-end, not a reimplementation. Two process
  lessons recorded directly: a search typo nearly caused a real,
  tested function to be reported missing, and a duplicate script was
  written before checking one already existed.
