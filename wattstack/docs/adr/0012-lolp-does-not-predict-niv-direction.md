# 12. LOLPDRM doesn't predict short-term NIV direction -- the BM-proxy plan needs a different signal

Date: 2026-08-07

## Status
Accepted -- a real, important correction to the roadmap, not a failed
experiment to quietly drop

## Context
The BM tightness-proxy plan (ROADMAP.md Phase B) hypothesized LOLP/
margin as the calibration signal, on the reasoning that it combines
demand and generation into one system-balance measure and should
therefore be a *better* tightness proxy than demand alone.

Tested against real historical data, multiple 7-day windows across
winter and summer, using `notebooks/demand_forecast_vs_system_tightness.py`'s
LOLP section (ADR 0010): LOLP sits at essentially zero across every
published forecast horizon (1h/2h/4h/8h/12h+), in both seasons
sampled. De-rated margin is comfortably positive throughout, with no
obvious relationship to `classify_system_length()`'s Long/Short
classification.

Both results are correct behaviour of the underlying metric, not a
data or pipeline problem, once the actual question each one answers
is separated from what NIV answers:

- **LOLP** is the probability that *total available generation
  capacity* cannot meet *total demand* at all -- a genuine capacity-
  adequacy emergency. A rare, extreme-tail event. GB runs with a
  comfortable margin under ordinary conditions; LOLP at ~0 across two
  unremarkable seasonal weeks is the metric working correctly, not
  failing to find a signal that was there.
- **De-rated margin** is a coarse, system-wide, day-ahead-ish "is
  there enough capacity in aggregate" figure.
- **NIV** (what `classify_system_length()` is built on) reflects
  routine, every-period balancing noise -- wind forecast error,
  ramping, unit trips -- the ordinary texture of running a real grid,
  completely unrelated in timescale and cause to capacity adequacy.

A system can have a very comfortable margin and still flip Long/Short
constantly period to period, because margin and NIV are answers to
different questions, not two measurements of the same thing at
different resolutions.

## Decision
LOLPDRM is not the right signal for period-to-period tightness
prediction, and the original hypothesis (LOLP/margin as strictly
better than demand alone for this purpose) is rejected based on real
data, not assumption. This is not treated as wasted effort -- it's
exactly what "explore against real data, in a notebook, before
anything reaches the optimizer" is supposed to do: catch a wrong
assumption before it gets baked into `core`, not after.

Two live paths forward, deliberately not chosen here:
1. Fall back to demand forecast alone (already validated as a real,
   if imperfect, single-variable proxy) and move to the acceptance-
   risk calibration work instead -- a separate roadmap item that
   doesn't depend on resolving this.
2. Pursue generation forecast directly, wind specifically (not yet
   confirmed against source) -- wind is the actual dominant driver of
   short-term forecast error in GB, much closer in kind to what
   drives NIV than a coarse adequacy measure is.

## Consequences
- ROADMAP.md's "Real, LOLP-calibrated pricing for bm_offer/bm_bid"
  item needs its premise corrected, not just its status updated --
  LOLP is not going to be the calibration signal it was planned to
  be.
- `loss_of_load_forecast()` and the notebook's LOLP section stay as
  built (ADR 0010) -- the client and the comparison tooling are
  correct and reusable for genuinely different future questions
  (capacity-stress analysis is a real thing LOLP *is* suited to,
  just not this one).
- Worth a general lesson, not just a specific one: a forecast that
  plausibly *sounds* like it should combine two signals into a
  better one doesn't necessarily answer the same question as the
  simpler signal it was meant to improve on. Checking what a metric
  actually measures, against real data, before trusting the
  intuition that it's "more information therefore better," is the
  actual discipline here -- the same shape of lesson as ADR 0006
  (BOALF sounding like it should have price, and not having it).
