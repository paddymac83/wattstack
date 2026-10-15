# 2. V1 optimizer: continuous LP via PuLP/CBC, daily-independent solves

Date: 2026-08-01

## Status
Accepted, with known follow-ups

## Context
The core optimizer needs to co-optimize a battery across energy
arbitrage and GB response markets (Dynamic Containment, Dynamic
Regulation, Dynamic Moderation), with SOC headroom reserved for any
response commitment. Two simplifications were made deliberately to
get a working v1 quickly, both of which trade some realism for speed
and simplicity.

## Decision
1. **Continuous LP, not MILP.** Charge/discharge exclusivity
   (preventing the battery from charging and discharging in the same
   half-hour period) is not enforced with a binary variable. This
   keeps every day's solve a small, fast LP (CBC solves it in
   milliseconds) and keeps the formulation easy to reason about and
   test. The cost: in periods with negative energy prices, the
   solver could in principle allow simultaneous charge and discharge,
   which no real inverter does. Not yet observed to matter on
   synthetic prices; worth checking against real Elexon data,
   especially the negative-price hours GB has seen more of recently.
2. **Each backtest day is solved independently**, with only the
   previous day's end-of-day SOC carried forward. This is effectively
   a perfect-foresight backtest at the day level, regardless of the
   `foresight` field currently sitting unused on `BacktestWindow`. A
   genuinely realistic rolling-horizon mode -- deciding today's
   day-ahead bid without seeing today's actual outturn -- is real
   future work.
3. **PuLP + bundled CBC**, not HiGHS, as the v1 solver. Zero-config,
   no separate binary to install, reliable in CI. HiGHS is the
   documented upgrade path if solve speed on larger sweeps/backtests
   becomes a bottleneck -- see the earlier chat log for why HiGHS was
   the original recommendation. PuLP's own API is mid-deprecation
   (`LpVariable.dicts` / `PULP_CBC_CMD` are being renamed for 4.0);
   pinned `pulp<4.0` in core/pyproject.toml to avoid a silent break,
   revisit when migrating.

## Consequences
Fast, easy-to-test v1. Known gaps (MILP exclusivity, real rolling
foresight, solver choice) are explicit here rather than silently
assumed. Revisit this ADR, don't just patch around it, when any of
the three becomes a real limitation.
