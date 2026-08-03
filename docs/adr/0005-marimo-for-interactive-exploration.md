# 5. marimo for interactive query-shaping, not Jupyter, not another script

Date: 2026-08-03

## Status
Accepted

## Context
ADR-level tension worth naming directly: an earlier decision on this
project was "script-based is preferable to notebook" for
`ingestion/`'s exploratory tooling, which is why `wattstack-explore`
exists as a CLI. But a different, real need showed up afterwards:
iteratively reshaping a query (e.g. "what fraction of settlement
periods was a battery the marginal bid") and seeing the plot update
immediately, before deciding the definition is worth promoting into a
tested feature. That's a fundamentally different workflow --
`wattstack-explore` re-runs its whole pipeline top to bottom every
time by design, which is exactly wrong for "tweak one thing, see the
result, tweak again" iteration.

The standard tool for that workflow is a notebook. The standard
objection to notebooks (unreviewable JSON diffs, hidden execution-
order state) is also real, and was the actual reason "script over
notebook" was the right call the first time.

## Decision
Use marimo, not Jupyter, for `notebooks/explore.py`. A marimo
notebook is a plain `.py` file (diffs like normal code, no JSON),
reactive (changing a cell re-runs everything downstream automatically
-- no manually re-running cells out of order and getting stale
state), and it's real Python: importable, and its cell graph can be
executed headlessly via `App.run()`, which is what
`tests/test_notebook.py` does to get automated regression coverage
over a notebook, not just an eyeball check.

The actual analysis logic (`filter_battery_bmu_ids`,
`marginal_bid_share`) lives in `analysis.py`, not in the notebook
itself, and takes field names as parameters rather than hardcoding
them -- deliberately, because the real Elexon acceptance-data schema
isn't confirmed yet (see ADR 0004). The notebook is where you
discover the real field names and bind them; `analysis.py` is what
gets promoted into `core`, properly typed with real field names
hardcoded, once a definition is worth shipping.

## Consequences
- `wattstack-explore` (batch, reproducible, CI-runnable) and
  `notebooks/explore.py` (interactive, exploratory) are both real and
  both stay -- they're not competing solutions to the same problem,
  they're solutions to two different problems that both showed up on
  this project.
- `App.run()`'s headless testing has a real limit: it proves the cell
  graph is wired correctly and that gated cells degrade gracefully
  (verified: buttons default unclicked, dependent cells absent from
  the namespace rather than partially populated) and, with mocked
  network responses, that the full reactive chain computes correctly
  end to end. It does not prove the real Elexon/NESO API calls work
  -- same live-traffic caveat as the rest of `ingestion/`.
- `marginal_bid_share()`'s definition (most extreme single accepted
  price per period) is explicitly not BSC's real price-setting
  methodology (volume-weighted across ~1% of accepted volume). It's a
  deliberately simple starting point to make interactively refining
  it in the notebook the next real step, not a finished metric.
