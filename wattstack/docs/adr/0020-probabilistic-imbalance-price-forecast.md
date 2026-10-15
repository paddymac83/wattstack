# 20. Probabilistic imbalance price forecast -- a real predictive model, not a seasonal average

Date: 2026-08-09

## Status
Accepted -- exploratory notebook built and validated on synthetic
data; NOT yet promoted to a production `PriceProvider`

## Context
The first genuine predictive-modelling effort in this project, moving
beyond the seasonal-average baselines built for wholesale (ADR 0017),
DC (ADR 0018), and BM (ADR 0019). Built from two real, read-in-full
sources, not from a title alone:

- Timera Energy, "The rising cost of system imbalance" (Mar 2026):
  *"the risk is in the distribution -- not the mean."* The average
  Day-Ahead-vs-imbalance spread is ~zero, because a competitive
  market arbitrages away any persistent bias. This is the same shape
  as a finding already made empirically in this project (ADR 0013/
  0014): wind predicts volatility, not direction, after demand, LOLP,
  and wind itself all failed to find a reliable direction signal.
  Not a coincidence -- both are describing the same market
  efficiency from different angles.
- Browell & Gilbert, "Predicting Electricity Imbalance Prices and
  Volumes" (Energies, 2022): a concrete day-ahead architecture --
  *"Separate density forecasts for long and short systems are
  combined according to the forecast probability of the system being
  long or short."* Also a genuine, load-bearing reality check: their
  own day-ahead model beat a simple climatological benchmark by only
  **3% MAE**. Their words: *"climatological or simple models perform
  well and are very difficult to improve on"* at the day-ahead
  horizon specifically -- real improvement only shows up intraday
  (5-40%). Expectations for this work are calibrated against that 3%
  figure directly, not against optimism.

A real correction made before building anything, confirmed with the
user directly: this targets **System Price** (the actual imbalance
settlement price), not the BOD submitted-price average
`ElexonBMPriceProvider` (ADR 0019) currently uses. Timera's own
framing -- *"the imbalance price is derived from the marginal action
taken within the BM"* -- makes System Price the more principled
quantity, since it already reflects which action was actually needed,
not just what was offered.

## Decision
- `probability_by_bin()` and `bucket_start_for_value()`, two new
  generic `analysis.py` primitives. The first converts a forecast
  input into a genuine empirical probability (not a classification --
  Browell & Gilbert are explicit that the day-ahead value is in the
  *probability* of system length, not a point prediction of it). The
  second looks up which trained bucket a new value falls into,
  falling back to the nearest available bucket rather than failing
  when a forecast lands outside the training window's observed range
  -- a real, deliberately handled edge case, not an oversight.
- `notebooks/imbalance_price_probabilistic_forecast.py`, implementing
  the full mixture architecture: `probability_by_bin()` trained on
  forecast demand vs. `classify_system_length()` (already validated
  against Elexon's published SPAR figures) gives `P(Short|bucket)`;
  real historical System Price split by realised length gives
  `F(price|Short)` and `F(price|Long)` as conditional means (and
  quantiles, computed for future risk-aware use, not yet consumed by
  anything).
- **A genuine chronological train/test split**, matching Browell &
  Gilbert's own methodology (their Jan 2017-Oct 2020 train / Oct-Dec
  2020 test) rather than a random split, which would leak future
  information backward. Proven by test, not just described
  (`test_train_test_split_is_chronological_and_disjoint`).
- **A real backtest against the flat seasonal-average baseline**,
  using MAE -- the same metric Browell & Gilbert report, specifically
  so their 3% figure is a meaningful comparison point. On data
  constructed to be perfectly separable, the mixture model achieves
  0.0 MAE against the flat baseline's non-zero MAE (100% improvement)
  -- proof the mechanism is *capable* of learning a real relationship,
  not evidence about what real GB data will show.
- **Deliberately not promoted to a production `PriceProvider` in this
  ADR.** Same "explore, then promote" discipline as every other real
  signal in this project -- LOLP was explored and rejected (ADR
  0012), wind was explored and validated for volatility (ADR 0013/
  0014); this needs the same real-data validation before anything
  built on it reaches `optimizer.py`.

## Consequences
- **A real, important negative result, confirmed live, not
  hypothetical:** the first version of this notebook, run against real
  GB data, gave a mixture-model MAE of £22.22/MWh against a flat
  baseline of £5.67/MWh -- a mixture model losing to a plain average
  by nearly 4x, not the modest improvement hoped for. Diagnosed rather
  than dismissed: `probability_by_bin()`'s raw empirical frequency has
  no protection against thin buckets -- a demand bucket with only 2-3
  historical observations can show an extreme rate like "100% Short"
  purely by chance, and a mixture model that confidently swings toward
  the Short-mean based on that noise is worse than an honest average.
  Being confidently wrong costs more than being uncertain -- a known
  statistical failure mode, not a flaw specific to this architecture.
- **Fixed with `shrink_probability_by_bin()`** (new, `analysis.py`):
  pulls a bucket's estimate toward the dataset's overall rate, in
  proportion to how few real observations support it -- a bucket with
  many observations is barely affected; a thin one is pulled strongly
  toward the population average. `shrinkage_strength` (a pseudo-count,
  default 10.0 in the notebook) is stated plainly as untuned, a
  starting point for empirical tuning against a real backtest, not a
  calibrated final answer. Proven live on realistic (non-perfectly-
  separable) mock data: without shrinkage, -9.5% vs baseline; with
  shrinkage, -0.7% -- correctly converging toward "no worse than
  baseline" rather than actively harmful, exactly the intended
  behaviour when the underlying signal is genuinely weak.
- A real diagnostic gap was closed alongside the fix, not just the
  probability formula: a new cell shows bucket sample sizes directly
  (reusing the already-existing `bin_counts_by_group()`), so a thin,
  unreliable bucket is visible before it silently corrupts a
  downstream forecast, not discovered only after a bad backtest
  result.
- `probability_by_bin()` itself was left completely unchanged --
  `shrink_probability_by_bin()` is an additive, parallel function
  (same return shape, a genuine drop-in), not a rewrite of
  already-tested, already-used code. `shrinkage_strength=0.0` recovers
  `probability_by_bin()`'s exact behaviour, proven by test.
- A real, pre-existing edge case in `bin_counts_by_group()` was
  rediscovered during validation, not newly introduced: when a
  bucketed value falls exactly on a bin-width multiple, `ceil()`
  doesn't round up and the existing clamp logic merges it into the
  bucket below. Already correctly tested from when that function was
  first built
  (`test_bin_counts_by_group_clamps_value_exactly_at_max_into_last_bin`)
  -- this ADR's validation work confirmed the behaviour is understood
  and deliberate, not undiscovered, and adjusted this notebook's own
  test fixtures to avoid the coincidence rather than changing
  long-standing, already-tested behaviour.
- **A shrinkage-strength sweep added to the notebook**, trying
  `[0, 1, 2, 5, 10, 20, 50, 100]` automatically against the same
  backtest logic and reporting MAE for each -- built so a real run
  can find a good value in one pass instead of manually adjusting a
  slider repeatedly.
- **Two real test-methodology bugs found and fixed while building the
  sweep's own tests, both worth recording:**
  1. An early version of the sweep's monotonicity test used
     `hash(day)` to generate a pseudo-random-looking Short/Long
     pattern. Python randomises string hash values per process by
     default (a security feature, not a bug) -- meaning that test's
     pass/fail depended on which random seed happened to be active on
     a given run, not on the actual shrinkage logic. Fixed by using
     `date.toordinal()` instead, a real integer, fully deterministic
     across every invocation. Confirmed stable across three separate
     runs with fresh random `PYTHONHASHSEED` values after the fix.
  2. Even after fixing the non-determinism, the test's own claim was
     wrong: strict monotonic improvement as shrinkage increases is
     NOT a property shrinkage actually guarantees -- a small,
     possibly-unlucky test set can make the raw, unshrunk estimator
     look good by chance on that specific set, the same way it can
     look bad. What shrinkage *does* mathematically guarantee is
     convergence to the dataset's unconditional rate as
     `shrinkage_strength -> infinity`, regardless of any single
     bucket's own data -- a real, provable property, now tested
     directly against `shrink_probability_by_bin()` itself
     (`test_shrink_probability_by_bin_converges_to_the_unconditional_rate_as_strength_grows_large`)
     rather than asserted indirectly and incorrectly through a
     notebook-level backtest.
- The notebook's honest framing (Browell & Gilbert's 3% day-ahead
  result stated directly in its own intro and printed alongside every
  backtest result) is a deliberate choice against overclaiming --
  whatever real GB data eventually shows, the notebook itself sets the
  bar to compare against, not an implied "this should obviously work"
  assumption.
- Conditional quantiles are computed but not yet used for anything --
  real future work is a risk-aware trading strategy in the shape of
  Browell & Gilbert's own Case 2/Case 3 (probabilistic hedging,
  VaR-constrained optimization), which the acceptance-risk /
  derating-factor work already on the roadmap is a simpler version of.
- What's next, not done here: **the real data has already been run
  once** (the £22.22/£5.67 result above) -- the shrinkage fix needs
  to be re-run against that same real data next, to see whether it
  closes the gap the way it did on synthetic noisy data, before any
  conclusion about whether this architecture works. Wind's validated
  volatility signal still isn't used in this architecture at all -- a
  real, named gap, not solved by the shrinkage fix. Only once a real
  run shows genuine improvement (anywhere near Browell & Gilbert's 3%,
  or better) does promoting this into a `PriceProvider` -- replacing
  or complementing `ElexonBMPriceProvider` -- become the right next
  step.
