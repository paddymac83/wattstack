# 27. DC multi-period SoC modelling, and a live-confirmed timing correction

Date: 2026-08-10

## Status
Accepted -- implemented

## Context
`docs/adr/0026` deliberately withheld this work, flagging a genuine
ambiguity in NESO's own worked example: whether the settlement period
immediately after a DC activation (spent assessing State-of-Energy and
preparing a replenishment baseline) is a delay separate from the
confirmed 1-hour gate, or already accounted for within it -- an
8-period vs 7-period total recovery window, both defensible readings
of the same prose.

Confirmed live, precisely: *"event in SP24, SP25 is idle in terms of
charging as the BESS calculates its SoC management plan, submits it at
the end of SP25 and waits across SP26 & SP27 before it can ramp its
baseline to begin SP28."* This settles it -- the idle
assessment/submission period (SP25) and the 1-hour gate (SP26-27) are
two genuinely distinct delays, not one. The correct total window from
a full-depletion event to full recovery is 8 settlement periods (1
idle + 2 gate + 5 recovery), not 7. `docs/adr/0024`'s original 7-period
figure undercounted by exactly one settlement period.

With the timing settled, the DC-only strategy pivot (`docs/adr/0026`)
made the requested multi-period SoC modelling buildable: with wholesale
and BM no longer active decisions, DC's own drain-then-recover
dynamics are the only source of SoC movement, removing the
cross-market interaction that would otherwise complicate reasoning
about it (though not the underlying difficulty that activation timing
remains stochastic and unknown at day-ahead planning time -- an
expected-value model, not an exact one, same honesty as everywhere
else acceptance/activation risk has been modelled in this project).

## Decision
- Constants corrected in `ingestion/wattstack_ingestion/analysis.py`:
  new `DC_ASSESSMENT_PERIOD = 1`, alongside the already-confirmed
  `DC_RECOVERY_GATE_PERIODS = 2` and `DC_RECOVERY_PERIODS_FROM_EMPTY = 5`.
  `DC_ACTIVATION_RECOVERY_WINDOW_PERIODS` is now their sum (8), not
  just gate + recovery (7). The existing test asserting the old figure
  was renamed and corrected, not left passing on a stale number.
- The same constants are duplicated (not imported) in
  `core/src/wattstack_core/optimizer.py`, as private module-level
  values -- `core` has zero dependencies on `ingestion` or `web`
  (pip-installable standalone, per the README), so `ingestion`'s
  `analysis.py` constants remain the source of truth for the
  underlying NESO research, but can't be imported into `core`
  directly. A comment in both locations points to the other, so a
  future correction to one is visible as a prompt to check the other.
- `_dc_recovery_weight(offset)` (new, `core/optimizer.py`): the
  confirmed piecewise shape -- `-1.0` at `offset=0` (the drain), `0.0`
  through the idle assessment and gate periods, `1/5` per period during
  the 5 confirmed recovery periods, `0.0` beyond the window (already
  fully recovered).
- `dc_activation_probability(day, market) -> list[float]` (new,
  optional `PriceProvider` extension, checked via `hasattr()`, same
  structural pattern as BM's `acceptance_probability()` -- and
  deliberately a different method name, not reused, since it measures
  a genuinely different thing: the probability of a real, energy-
  depleting activation *event*, not the probability of a bid being
  selected in an auction). Defaults to `0.0` -- the same safe,
  backward-compatible default philosophy as `acceptance_probability`,
  preserving DC's pre-existing zero-SoC-impact behaviour exactly for
  any provider that hasn't opted in.
- The SoC recursion gains a new term: for each period `t`, sum over
  every prior period `t_prev` within the confirmed 8-period window of
  `reserve[m][t_prev] x 0.25 (minimum energy requirement hours) x
  dc_activation_probability[m][t_prev] x _dc_recovery_weight(t - t_prev)`,
  sign-flipped for charge-direction markets (DC-High) relative to
  discharge-direction ones (DC-Low) -- DC-Low's activation drains SoC
  and its recovery charges it back up; DC-High's activation charges
  SoC up and its recovery discharges it back down, the exact mirror
  image, proven directly by test.
  (`test_dc_high_activation_and_recovery_are_the_mirror_image_of_dc_low`).
- Deliberately NOT efficiency-scaled (no `*eff`/`/eff` on this new
  term) -- a stated simplification, not an oversight: the minimum-
  energy magnitude is already a conservative, worst-case estimate, and
  splitting this multi-period, bidirectional term by charge/discharge
  sign to apply efficiency correctly would add real complexity for a
  second-order refinement.
- Headroom and footroom remain based on the *full*, un-derated
  `delivery_hours` commitment, exactly as BM's version of this
  discipline already established (`docs/adr/0023`) -- a low activation
  probability must never justify holding less physical margin than the
  worst case needs. Proven directly by test, not assumed by analogy.
- `DispatchResult.dc_activation_probability` (new field, `results.py`)
  reports what was actually used, mirroring `acceptance_probability`'s
  own reporting -- kept as a separate field, not merged with it, since
  the two measure genuinely different things and conflating them would
  misrepresent what either one means.

## Consequences
- Proven by test, not just described: the drain lands exactly at the
  activation period; periods at offsets 1-3 show zero change (the idle
  assessment period and gate, together); recovery begins precisely at
  offset 4, not 1, 2, or 3; the 5 recovery increments sum to exactly
  the original drain (full restoration, no over- or under-correction);
  DC-High is the exact sign-mirror of DC-Low; and headroom/footroom
  stay fully un-derated throughout.
- All 47 pre-existing core tests passed unchanged both before and
  after this change -- the new mechanism's default (`0.0` activation
  probability for any provider that hasn't implemented the extension)
  preserves every existing test's assumption exactly, not just
  approximately.
- A real, live-confirmed correction to `docs/adr/0024`'s own figure --
  recorded here plainly rather than quietly editing that ADR's
  history. The lesson carried forward: even a careful reading of a
  primary source, done once, can still miss a real distinction that a
  second, more careful pass (or a direct confirmation) catches --
  worth remembering next time a similarly precise multi-step
  regulatory timing needs encoding into LP logic.
- What remains real, un-modelled complexity, stated directly rather
  than implied solved: `dc_activation_probability` itself is
  unresolved -- no attempt is made here to derive it from anything
  (largest-loss data, ADR 0016, remains the most plausible real
  signal, still not built into an estimate). The efficiency-scaling
  simplification above is a real approximation, not a precise model.
  And this remains an expected-value treatment of a stochastic event,
  not the exact, path-dependent constraint that would fully capture
  DC's actual regulatory obligations -- deliberately out of scope,
  same reasoning as the two-stage/stochastic-program discussion
  already in `docs/adr/0023`.
