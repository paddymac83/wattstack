# wattstack roadmap

Status: pre-v1. This file is the running plan -- update it as scope
actually changes, don't let it drift from what's true. Each backlog
item should link back to an ADR once real design decisions get made
for it, the same way v1's own decisions already live in
`docs/adr/0001`-`0006`.

## v1 -- first release

**Goal:** a small, honest, educational tool for understanding how a
GB battery's physical capabilities translate into revenue across
wholesale energy and response markets -- not a production trading
system, not a promise of achievable returns.

In scope:
- [ ] Real energy prices: a real `ElexonPriceProvider` (implementing
      `core`'s `PriceProvider` protocol) replacing `SyntheticPriceProvider`
      as the default in `web/`. `ingestion/`'s `ElexonClient` already
      does the fetching; this is about wiring it into `core` properly,
      with `verify_schema()`-style safety carried over.
- [ ] Real response prices: same, for NESO DC/DR/DM via a
      `NesoPriceProvider`, once `NESO_PRICE_FIELD` is confirmed (see
      `ingestion/wattstack_ingestion/cli.py`).
- [ ] "How this works" panel in the web UI, stating plainly: results
      assume perfect foresight *within* a day (see ADR 0002), and the
      market set is Energy + DC/DR/DM only (no BM/reserve yet). A
      ceiling, not a promise -- said in the app, not just in an ADR a
      developer would read.
- [ ] Quickstart docs aimed at someone who just wants to run the app,
      separate from the (now fairly dense) developer-oriented README.
- [ ] *Optional, cheap:* static Capacity Market calculation (de-rating
      curve x clearing price) -- no optimizer change, rounds out the
      revenue-stack story. Include if it doesn't threaten "minimal."

Explicitly out of v1 -- see backlog below for why each is safe to defer.

## Backlog, sequenced (not just a list -- order matters)

### 1. BM + wholesale + reserve stacking
Expand `markets.py`'s model:
- split today's single "Energy" stream into day-ahead wholesale vs
  BM/imbalance as genuinely separate decisions
- add reserve services (BR/QR/SR) alongside today's response services
  (DC/DR/DM) -- NESO's EAC platform procures both through the same
  system now (see `ingestion/`'s `neso.py` and ADR on confirmed
  resources)

Goes first because items 2 and 3 below are more meaningful once BM is
a real, separate stream -- doing them against today's simplified
two-stream model means redoing chunks of it later.

### 2. Rolling horizon + imperfect price capture
- replace day-independent, perfect-intraday-foresight solves (ADR
  0002) with a genuine day-ahead commitment + real-time adjustment
  loop
- needs *some* forecast basis for the day-ahead decision -- start
  naive (e.g. "yesterday's actual prices"), labeled naive, not a real
  forecasting model
- "imperfect price capture" (execution slippage, minimum bid size,
  bid-ask spread) fits naturally once there's a real bid/execution
  split to attach it to -- doesn't make sense before item 1 and this
  exist

### 3. Sensitivity analysis, including skip rates
- extend the existing duration sweep (`run_sweep()` in
  `core/backtest.py`) into a proper report: duration, efficiency,
  market on/off combinations
- skip rates specifically need real acceptance-rate data -- natural
  extension of the marginal-bid-share pattern already proven out in
  `ingestion/analysis.py`, and only meaningful once a real BM stream
  (item 1) exists to be skipped *from*

## Known limitations already tracked elsewhere

Don't duplicate these here -- most of the backlog above is the
follow-up work these already flagged:
- `docs/adr/0002` -- optimizer simplifications (continuous LP, daily-
  independent solves, PuLP/CBC choice)
- `docs/adr/0004` -- ingestion built against researched, not fully
  live-tested, API shapes
- `docs/adr/0006` -- BOALF is volume, not price; BOD is price
