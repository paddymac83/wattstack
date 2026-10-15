# 21. ElexonImbalancePriceProvider: the probabilistic model, promoted

Date: 2026-08-09

## Status
Accepted -- implemented

## Context
`docs/adr/0020` built and validated the probabilistic mixture
architecture in an exploratory notebook, deliberately not promoted to
a `PriceProvider` until real data confirmed it worked. That real run
happened: the original bad result (£22.22/MWh mixture MAE against a
£5.67/MWh flat baseline) was mis-diagnosed as a thin-bucket
overconfidence problem needing shrinkage. The actual cause, found by
directly increasing the training window: **the original training set
was only 1-2 days**, far too little for any model, shrunk or not, to
learn anything real. With a realistic 60-day training window and a
12-day chronological holdout, the unshrunk mixture model achieved a
**2.3% MAE improvement** over the flat baseline -- close to Browell &
Gilbert's own published day-ahead result (3%), a genuine match to
peer-reviewed literature on real GB data, not just a synthetic
demonstration.

This corrects ADR 0020's own working hypothesis, stated there as fact
without yet being tested against real data at realistic scale --
worth recording plainly rather than quietly editing history: shrinkage
was a real, principled, tested fix for a real failure mode
(demonstrated on synthetic data), but it was not, in the end, *this*
problem's fix. More training data was.

## Decision
- `ElexonImbalancePriceProvider` (`ingestion/wattstack_ingestion/prices.py`),
  implementing `reserve_prices()` for `BM_OFFER`/`BM_BID`, promoted
  directly from the validated notebook logic -- same training/predict
  structure (join demand forecast + realised System Price + system
  length over a lookback window, train `probability_by_bin()` plus
  conditional means, predict via the target day's own forecast
  demand), not a rewrite.
- **No shrinkage.** `probability_by_bin()` (raw), not
  `shrink_probability_by_bin()`. `shrink_probability_by_bin()` stays
  in `analysis.py`, fully tested, available for anyone using a much
  shorter lookback in the future -- removed from this provider
  specifically because it wasn't the fix for the problem that was
  actually diagnosed, not because it's broken.
- Default `lookback_days=60`, matching what was validated -- a real
  cost consideration named directly in the class docstring: ~120
  requests per `reserve_prices()` call before caching (one demand-
  forecast call and one system-prices call per training day).
- **`ElexonBMPriceProvider` (BOD-average, ADR 0019) is kept in the
  module, not deleted.** A known, tested alternative; no longer the
  recommended default. `ElexonImbalancePriceProvider` targets System
  Price directly, the more principled quantity per Timera Energy's own
  framing (ADR 0020) -- this is the intended, permanent replacement in
  the "recommended" wiring, not a parallel option to choose between
  arbitrarily.
- **A real, stated simplification, not resolved here**: `BM_OFFER` and
  `BM_BID` currently receive the identical forecast (the unconditional
  mixture mean for that period) -- proven directly by test
  (`test_reserve_prices_gives_the_same_forecast_for_bm_offer_and_bm_bid`),
  so a future change to differentiate them is a deliberate choice
  visible in a failing test, not a silent regression. The natural
  refinement -- weighting `BM_OFFER` toward the Short-conditional mean
  (since that's when discharge capacity is actually likely to be
  called) and `BM_BID` toward the Long-conditional mean -- is named
  directly in the class docstring as real future work.

## Consequences
- This is the first genuinely predictive (not seasonal-average) price
  provider reaching `optimizer.py` in this project. `ElexonWholesalePriceProvider`
  and `NesoDCPriceProvider` remain seasonal averages; only BM's price
  now comes from a validated statistical model.
- The day-ahead trigger convention (10:00 UTC the day before) is
  reused exactly, both for the training window's historical demand
  forecasts and for the target day's own forecast -- proven by test
  that the actual `publishTime` values sent match the confirmed
  convention, not just that the provider runs.
- `CombinedPriceProvider`'s existing `reserve_providers` list
  mechanism (ADR 0019) needed no changes to accommodate this --
  `ElexonImbalancePriceProvider` self-declares its covered markets by
  raising `ValueError` for anything else, the same contract every
  other reserve provider already follows.
- What's still open, named directly rather than implied solved: the
  offer/bid symmetry simplification above, and whether 2.3% actually
  translates into materially different optimizer decisions once wired
  into a real stacked run -- a real backtest of realised *revenue*,
  not just forecast MAE, is the next honest check, not assumed from
  the MAE figure alone.
