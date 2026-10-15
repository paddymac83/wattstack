# 14. Wind forecast validated for volatility; this splits BM signal work into two threads

Date: 2026-08-07

## Status
Accepted -- the first positive result in this line of investigation,
worth noting explicitly as a contrast to ADR 0012's rejection

## Context
Tested against real data (`docs/adr/0013`'s notebook section): above
roughly 4GW forecast wind, actual System Price shows meaningfully
higher volatility (price dispersion). There is no strong relationship
between forecast wind and system direction (Long/Short).

Both halves make sense together, not separately:

- Wind's actual output can deviate meaningfully from forecast --
  weather forecast error, ramping speed, curtailment -- in a way
  dispatchable generation mostly can't. When wind's forecast share is
  high, the same *percentage* error becomes a much larger *absolute*
  MW swing, which is what moves price.
- That error is roughly symmetric, not systematically biased one
  direction. A well-calibrated forecast having no directional lean is
  the expected, correct behaviour -- not a second disappointment
  alongside LOLP's rejection.

The 4GW threshold itself hasn't been checked beyond the winter/summer
sample -- worth confirming it holds with more data before treating it
as a precise cutoff rather than an approximate regime change.

## Decision
A signal that predicts volatility but not direction answers a
different question than the one the original BM-proxy plan was built
around. Direction would inform which way to bias `bm_offer` vs
`bm_bid` pricing -- that piece is still open (demand: weak signal;
LOLP: rejected; wind: doesn't help here either). Volatility informs
something the project hasn't had a signal for: the *option value* of
staying flexible. Higher expected volatility means more value in
holding capacity in reserve for opportunistic real-time response,
independent of which direction that response ends up being -- the
same logic as financial option pricing, where value rises with
uncertainty regardless of direction.

This splits what was one roadmap item into two independent threads,
not one blocked on the other:
1. **Direction/bias calibration** -- still needs a working signal.
   Not resolved here.
2. **Volatility-informed capacity reservation** -- wind is validated
   as the input; how it actually translates into reserved headroom/
   footroom in the day-ahead plan is real design work, not done yet.

## Consequences
- No code changes from this ADR alone -- this is a finding and a
  design-thread split, recorded before either thread gets built, not
  after. Consistent with the project's standing discipline: a real
  result changes the plan before it changes the code.
- The mocked test suite (ADR 0013) already proves the mechanism that
  produced this finding is sound -- this ADR is about what the
  finding *means*, not re-verifying that it's real.
- Worth remembering going forward: not every validated signal answers
  the question it was originally sought for. Wind was pursued as a
  tightness/direction candidate and turned out to answer a different,
  still-useful question instead. Judging a signal by what it actually
  measures, against real data, rather than discarding it for not
  fitting the original slot, is the same discipline that made LOLP's
  rejection informative rather than a dead end.
