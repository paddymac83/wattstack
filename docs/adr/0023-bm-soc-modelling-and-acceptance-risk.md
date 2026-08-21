# 23. BM SoC modelling via expected energy delivery, and acceptance-risk pricing

Date: 2026-08-10

## Status
Accepted -- implemented (SoC modelling, derating factor); acceptance-
risk sophistication is deliberately future work, discussed at length
below rather than built speculatively.

## Context
Every reserve market in this project's optimizer, until now, had zero
direct effect on state of charge -- only the headroom/footroom
constraints (worst-case margin needed *if* a commitment were called).
This was a real gap, not a deliberate simplification stated as such:
BM, when actually called, genuinely discharges or charges the battery
for the delivery duration, and that energy transfer affects every
subsequent period's available capacity. Treating BM identically to
DC's true availability-payment structure (already named as a known
approximation in `MarketSpec`'s own docstring) meant the optimizer
could commit BM capacity in every single period without ever "paying"
for it in SoC terms -- visible directly in a real run: BM-Offer at
full power for 42 consecutive periods with SoC sitting flat throughout.

A real, sourced fact closes a gap in this reasoning rather than just
motivating it: NESO's MEL/MIL rule change (11 March 2024, per Modo
Energy's reporting) moved battery declared availability from a
15-minute to a 30-minute sustained-delivery basis, specifically
because most real BM dispatch actions run 30 minutes -- 76% of energy
dispatched in H1 2023 was for actions longer than 15 minutes, with 30
minutes the single most common duration. This means a battery, once
called, is genuinely deliverable for up to the full settlement period
-- confirming, not just assuming, that `MARKET_REGISTRY`'s existing
`delivery_hours=0.5` for BM was already the right number, and that a
called BM commitment can be modelled at the same period granularity as
everything else in the optimizer, with no sub-period resolution
needed.

Separately, `ElexonImbalancePriceProvider` (ADR 0021/0022) had no
acceptance-risk discount at all -- unlike `ElexonBMPriceProvider`,
which it superseded and which had an explicit, if arbitrary, 0.3
`acceptance_derating`. A real run showed the consequence directly: BM
prices (£56-93/MWh, no discount for "will NESO actually call *my*
specific bid") heavily outcompeted DC's real, auction-cleared prices
(£0.42-8.96/MWh, which already has competition priced in by
construction), plausibly for the wrong reason.

## Decision
- **`acceptance_probability(day, market) -> list[float]`**, an
  optional `PriceProvider` extension, deliberately NOT added to the
  formal `Protocol` in `core/prices.py` -- adding a required method
  there would force every existing implementation (including
  `SyntheticPriceProvider` and every wholesale/DC provider, which have
  no acceptance-risk concept at all) to implement something not
  meaningful for them. Checked structurally via `hasattr()` in
  `optimizer.py`, the same duck-typing pattern already used throughout
  this project (e.g. `market.name` dispatch in the ingestion layer).
- **Default: 0.0, not 1.0**, for any market/provider that doesn't
  implement it. This is the deliberately safe choice: 0.0 means "no
  expected energy delivery from this commitment," which is exactly
  the pre-existing behaviour, preserved exactly for `SyntheticPriceProvider`,
  `ElexonBMPriceProvider`, and anything else that hasn't opted in --
  proven by test that every existing core test still passes unchanged,
  and by a new explicit test
  (`test_without_acceptance_probability_soc_is_unaffected_by_bm_commitments`).
- **Only energy-settled (`settlement_unit == "per_mwh"`) reserve
  markets get this treatment** -- BM-Offer/BM-Bid, never DC
  ("per_mw_h", availability-style). Proven by test that DC never
  appears in `DispatchResult.acceptance_probability` even when the
  supplied provider would answer for any market if asked
  (`test_dc_markets_never_appear_in_acceptance_probability_even_when_provider_implements_it`).
  Headroom/footroom remain unchanged -- still the full,
  un-probability-weighted worst case, proven by test
  (`test_headroom_and_footroom_remain_based_on_full_delivery_hours_not_derated_by_acceptance`):
  a low acceptance probability must not be used to justify holding
  less margin than the full commitment would need if it *was* called.
- **The SoC recursion gains an expected-energy term**: `reserve[m][t]
  x delivery_hours x acceptance_probability[m][t]`, split by direction
  and run through the same round-trip efficiency split (`eff`,
  `1/eff`) wholesale charge/discharge already uses -- the same
  physical battery, same losses, not a separate accounting. Proven
  correct two ways: an isolated-period test where BM-Offer is only
  attractive in one period, making the resulting SoC drop calculable
  by hand and matched to the formula exactly
  (`test_isolated_period_soc_drop_matches_the_expected_energy_formula_exactly`),
  and a general test reconstructing `soc[t]` from the actual solver
  output across all 48 periods and confirming it satisfies the
  recursion, independent of what specific decisions the solver made
  (`test_soc_recursion_holds_exactly_across_every_period_with_both_bm_markets_active`).
- **`DispatchResult` gains `acceptance_probability: dict[Market, list[float]]`**,
  carried alongside the result the same way `wholesale_price`/
  `reserve_price` already are -- present for any active BM market
  regardless of whether its provider implements the extension (zero-
  filled when it doesn't), so downstream consumers can tell whether
  real acceptance-risk modelling was actually used, not just guess.
- **`ElexonImbalancePriceProvider` gains `derating_factor`** (default
  0.3, the same starting point `ElexonBMPriceProvider` used), applied
  multiplicatively to the final forecast in `reserve_prices()`, and
  exposed identically via the new `acceptance_probability()` method --
  deliberately the SAME number driving both the revenue discount and
  the SoC/energy impact, not two independently-tunable values that
  could silently drift apart from each other.

## Consequences
- This is a real, structural change to dispatch physics, not a
  cosmetic addition -- a battery holding BM-Offer capacity now
  genuinely drains SoC in expectation, which will change what the
  optimizer chooses to commit in later periods once energy runs low,
  the same way real BM delivery would. Not yet checked against a real
  live run with both changes active together -- worth doing before
  trusting the resulting dispatch decisions in anger.
- `derating_factor=0.3` remains exactly as uncalibrated as
  `ElexonBMPriceProvider`'s was -- a stated starting point, not a
  fitted number. See the discussion below for what a real calibration
  would need.

## Future work: acceptance risk is genuinely more subtle than one flat number, and applies to all four markets, not just BM

The user's own framing is worth engaging with directly: *there is risk
in all four markets of non-acceptance which needs to be factored in
with price forecasts, alongside SoC considerations, to reach a truly
optimal decision.* This section takes that seriously rather than
treating `derating_factor=0.3` as a closed question.

**Two genuinely different risks are currently conflated into one
number for BM.** `ElexonImbalancePriceProvider`'s `P(Short)`/`P(Long)`
already prices in "was the regime forecast correct" -- a real,
validated signal (ADR 0020/0021). `derating_factor` is meant to price
a *separate* risk: even in a period where the regime forecast is
exactly right, was *this specific* bid or offer the one NESO actually
selected, among however many competing units offered the same
direction? A flat 0.3 applied uniformly cannot distinguish a period
where the regime forecast is highly confident from one where it's
genuinely uncertain, and cannot reflect that acceptance likelihood
plausibly *correlates* with the regime signal itself -- a period with
high `P(Short)` is precisely a period where NESO more urgently needs
BM-Offer actions, which plausibly raises (not just co-occurs with) the
chance any given offer gets called. A more principled version would
model `acceptance_probability` as a function of the regime forecast
already being computed, not an independent constant multiplied on top
of it.

**DC currently has zero acceptance-risk modelling, and that's a
real gap, not a non-issue.** DC's settlement (`per_mw_h`) genuinely is
paid on clearing regardless of being called within the delivery
period -- but *clearing the DC auction at all* is itself a
competitive, uncertain process at bid-submission time, which this
project's DC price (a seasonal average of historical clearing prices,
ADR 0018) doesn't distinguish from "definitely wins if bid." A more
complete treatment would price the probability of the *bid itself*
clearing, separately from the probability of being *called* once
cleared -- a different risk than BM's, at a different decision point,
not currently modelled at all.

**A real calibration path exists and isn't built here.** BOD (bid/
offer submitted prices) joined with BOALF (acceptance volumes and
timing, confirmed price-free per ADR 0006) would give genuine,
empirical acceptance rates -- by period, by price level, by how far a
submitted price sat from what was actually accepted -- replacing a
guessed constant with data the same way DC's price and BM's regime
forecast both already replaced guesses with real historical patterns.
Not attempted here: this ADR adds the *mechanism* (acceptance
probability flowing through to both revenue and SoC), deliberately
leaving *calibration* as separate, real future work rather than
rushing an under-evidenced number into place.

**The deepest version of this is a genuinely different kind of
optimizer, not a better price signal.** The current model is a
deterministic-equivalent LP: one scenario, expected values throughout.
It cannot represent "I don't know whether I'll be called, so it's
worth holding some capacity flexible across multiple markets rather
than committing fully to the one with the best expected price" --
that's a hedging/optionality decision a single-scenario expected-value
LP structurally can't see, since expectation-maximisation with a fixed
set of decision variables per period collapses exactly the uncertainty
that would make hedging valuable. A genuinely risk-aware version would
need either a proper two-stage (or multi-scenario) stochastic program
-- decide reserve commitments now, under uncertainty; decide actual
dispatch later, once acceptance is known -- or the VaR/CVaR-constrained
approach Browell & Gilbert's own paper builds its trading-strategy case
studies around, using the full forecast *distribution* (already
computed as conditional quantiles in this project's notebook, ADR
0020, currently unused) rather than a single point probability. Both
are real, substantial future work, not incremental extensions of what
exists today -- named here so the gap is visible, not implied solved
by adding one derating parameter.
