# 24. DC State-of-Energy management, confirmed precisely, and an activation risk premium

Date: 2026-08-10

## Status
Accepted -- implemented (activation risk premium as a pricing input);
a full LP-level recovery constraint is deliberately future work,
discussed below rather than attempted here.

## Context
Following the strategy discussion on day-ahead timing (wholesale gate
closure 09:50, DC/DM/DR/BR/QR gate closure 14:00, confirmed directly
from NESO's own June 2026 Balancing Reserve Guidance Document -- an
older document showed 14:30, which predates EAC's consolidation of
these services into one auction), the user asked specifically to
formalise DC's State-of-Energy (SoE) management rules, sourced from
NESO's own Dynamic Containment Guidance Document
(https://www.neso.energy/document/175296/download), fetched and
verified directly rather than worked from the earlier partial search
snippet.

Every figure the user described going in was confirmed exactly against
the primary source:

- **Minimum energy requirement**: 15 minutes at full contracted power
  -- `(15/60) x contracted_MW`. NESO's own example: 50MW -> 12.5MWh.
- **Minimum recovery rate**: 20% of the minimum energy requirement per
  settlement period, exactly 3 minutes at full power --
  `(3/60) x contracted_MW`. 50MW -> 2.5MWh/SP.
- **The 1-hour gate**: confirmed directly -- *"there is a 1 hour gate
  before baselines can apply -- this is the convention applied to
  physical notifications in the BM."* A worked example traces it
  exactly: energy assessed at the start of SP25, a new baseline
  submitted before the end of SP25, takes effect from SP28 -- 2 SPs
  (1 hour) after submission.
- **Ramp rate**: 5% of contracted MW per minute -- 2.5MW/min for 50MW.
- **Headroom**: confirmed, and NESO deliberately doesn't prescribe an
  amount -- *"a unit with name-plate capacity of 50MW cannot be
  contracted to deliver 50MW of DC -- it must retain some headroom...
  We can only be sure that a xMW capacity unit can offer < xMW of
  DC."* Left to the provider's own judgement.
- **Real downside if depleted twice before recovering**: the unit goes
  unavailable (unpenalised, if SoE rules were followed) until SoE is
  restored or SP32, whichever comes first.

A genuine, algebraic finding worth recording: the number of settlement
periods needed to fully recover from empty is always exactly 5,
**regardless of contracted MW** -- both the minimum energy requirement
(`0.25 x MW`) and the recovery rate (`0.05 x MW` per SP) scale with
contracted MW identically, so the ratio (`0.25/0.05 = 5`) is constant.
Total window from a full-depletion event to full recovery: 2 (gate) +
5 (recovery) = 7 settlement periods, 3.5 hours.

A separate, real discrepancy surfaced while reviewing this against the
existing model: `MARKET_REGISTRY`'s `delivery_hours=0.5` for DC
assumes headroom/footroom must back a full 30-minute sustained
commitment, but NESO's actual minimum energy requirement is 15
minutes. The existing model may be more conservative than strictly
required. Not changed here -- a deliberate decision about how much
margin to keep is worth making on purpose, not silently altered as a
side effect of this work.

## Decision
- `DC_MINIMUM_ENERGY_REQUIREMENT_HOURS` (0.25), `DC_RECOVERY_GATE_PERIODS`
  (2), `DC_RECOVERY_PERIODS_FROM_EMPTY` (5), and
  `DC_ACTIVATION_RECOVERY_WINDOW_PERIODS` (7) -- named constants in
  `analysis.py`, not magic numbers, each directly traceable to the
  source document's own confirmed figures.
- `dc_activation_risk_premium()`, a new `analysis.py` function --
  **an expected-value pricing input, not an LP constraint.** Given
  contracted MW, a degradation cost per MWh, the wholesale price
  series covering the 7-period recovery window, and an
  `activation_probability`, returns a recommended addition to a DC
  floor bid (£/MW/h, matching DC's own units). Two costs, both scaled
  by the confirmed 15-minute minimum energy requirement (a
  deliberately conservative worst-case magnitude, not the energy
  actually delivered in any specific real event):
  1. Degradation -- straightforward, cost per MWh cycled.
  2. Foregone wholesale opportunity during the mandatory recovery
     window -- approximated as the price spread (max-min) across the
     given window, a deliberately simple, conservative proxy for lost
     arbitrage value, not a precise calibration. Proven by test that
     this genuinely reflects spread, not average price level
     (`test_dc_activation_risk_premium_uses_price_spread_not_average`):
     two windows with the same mean but different spread give
     different premiums.
- `activation_probability` is passed in, not derived -- explicitly
  unresolved here, same treatment as `derating_factor` elsewhere in
  this project. The most plausible real signal is largest-loss data
  (ADR 0016), not yet built into an actual estimate.
- **Deliberately not a new LP constraint.** An exact, conditional
  recovery constraint would need to track "was DC activated last
  period, and if so, how much of the mandated recovery schedule
  remains" -- a genuinely stochastic, path-dependent state that a
  deterministic-equivalent day-ahead LP can't cleanly represent. This
  is the same two-stage/stochastic-program territory already named as
  real future work in ADR 0023 -- not attempted here, named directly
  rather than rushed.

## Consequences
- This gives a real, sourced number to compare a DC bid against, not
  just intuition about "should probably price in some risk" -- worth
  running with real wholesale price data and a real
  `activation_probability` estimate before trusting the resulting
  floor price in anger; not yet done here.
- `activation_probability`'s absence of a real estimate is the biggest
  open gap in this whole calculation -- the premium is only as good as
  that number, and no calibration work has been done for it here
  (largest-loss data, ADR 0016, remains the most plausible real
  signal, unbuilt).
- The `delivery_hours=0.5` vs 15-minute-requirement discrepancy
  remains unresolved, named directly rather than silently fixed --
  worth a deliberate decision, not a side effect of this ADR.
- The exact multi-period recovery constraint (tracking real SoE state
  through a stochastic activation event) remains real, substantial
  future work -- the two-stage/stochastic program direction already
  named in ADR 0023 is the natural home for it, not attempted here.
