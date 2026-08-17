# 22. Asymmetric BM_OFFER/BM_BID pricing, replacing the shared blend

Date: 2026-08-09

## Status
Accepted -- implemented

## Context
`docs/adr/0021` promoted `ElexonImbalancePriceProvider` with a stated,
deliberate simplification: both `BM_OFFER` and `BM_BID` received the
identical unconditional mixture mean for a given period, pinned by a
test named specifically to make the simplification visible rather
than accidental. That ADR also named the natural refinement directly:
weight each market toward its own relevant conditional mean, since the
two markets aren't economically symmetric.

The reasoning, stated plainly: `BM_OFFER` (discharge) is only
genuinely valuable when the system is Short -- NESO needs more
generation, not less, and that's when discharge capacity would
actually be called. `BM_BID` (charge) is the mirror image, valuable
only when Long. A period's blended mixture mean averages these two
economically distinct situations together, discarding exactly the
information that would let `BM_OFFER` and `BM_BID` be priced
differently for the same period -- which they should be, since their
underlying value genuinely differs.

## Decision
- Each market's forecast is now `P(its own regime) x mean_price_in_
  that_regime`: `BM_OFFER` gets `P(Short) x mean_price_given_short`,
  `BM_BID` gets `P(Long) x mean_price_given_long`. An implicit ~0
  contribution from the "wrong" regime, not a partial/discounted one
  -- a real economic modelling choice (capacity held in the
  off-regime generally isn't what NESO is calling for), stated as
  such in the class docstring, not asserted as definitively correct.
  Worth revisiting if real acceptance data later shows meaningful
  off-regime value for either market -- not assumed here without
  evidence.
- Both markets still share the same trained `probability_by_bin()`
  table and conditional means -- only the final per-market formula
  differs, not the training step. No duplicated data fetching or
  training logic between the two market paths.
- **The fallback behaviour was also upgraded, not just left as-is**:
  a period whose own forecast demand is missing (but training data
  otherwise exists) now falls back to the dataset's *overall*
  Short/Long rate, applied through the same per-market formula --
  proven by a dedicated test
  (`test_reserve_prices_period_with_missing_demand_uses_overall_rate_not_zero`)
  that this differs from the all-data-missing case, which still
  correctly returns 0.0 throughout. The previous version's fallback
  (a flat `unconditional_mean` for both markets) would have silently
  reintroduced the shared-blend problem this ADR removes, exactly in
  the cases where a robust fallback matters most.
- The three tests that pinned the old shared-blend behaviour
  (`test_reserve_prices_correctly_forecasts_the_target_day_on_separable_data`,
  `test_reserve_prices_gives_the_same_forecast_for_bm_offer_and_bm_bid`)
  were rewritten, not just updated in place -- their old assertions
  described behaviour this ADR deliberately removes, and keeping them
  passing by coincidence would have been worse than making the change
  visible through failing, then rewritten, tests. Replaced with three
  tests that check the new behaviour directly: `BM_OFFER` responds
  only to Short probability, `BM_BID` only to Long, and the two
  genuinely differ for the same period on real (mocked) separable
  data.

## Consequences
- The optimizer now sees a real asymmetry between the two BM markets
  for the same period, where it previously saw an identical number --
  a period confidently forecast Short should now show `BM_OFFER`
  priced meaningfully above `BM_BID`, and vice versa for a confidently
  Long period. Whether this changes actual dispatch decisions in a
  real stacked run hasn't been checked here -- a real question for
  the next live run, not assumed from the pricing formula alone.
- The zero-contribution-from-the-wrong-regime assumption is a genuine
  simplification, named as such. A less extreme version (some partial
  value in the off-regime, not exactly zero) is a plausible future
  refinement if real data ever supports it -- not built speculatively
  here.
