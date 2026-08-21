"""The core LP: one day's optimal dispatch across wholesale and any
active MARKET_REGISTRY markets, with state-of-charge headroom or
footroom reserved for any commitment that period is exposed to,
depending on that market's direction.

This is deliberately a linear program, not a mixed-integer one --
charge/discharge exclusivity is not enforced. That keeps solves fast
and the model easy to reason about, at the cost of occasionally
allowing simultaneous charge and discharge when energy prices are
negative. See docs/adr/0002 for why, and what to revisit later.
"""
from __future__ import annotations

from datetime import date

import pulp

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import MARKET_REGISTRY, SETTLEMENT_PERIOD_HOURS, Market
from wattstack_core.prices import PERIODS_PER_DAY, PriceProvider
from wattstack_core.results import DispatchResult, TwoStageResult

# DC's confirmed State-of-Energy recovery timing, duplicated here
# rather than imported -- core has zero dependencies on ingestion or
# web (pip-installable standalone, see README). These values must stay
# in sync with ingestion/wattstack_ingestion/analysis.py's own DC_*
# constants, which remain the source of truth for the underlying NESO
# document research (docs/adr/0024, corrected in docs/adr/0027 after
# an initial undercount: the idle assessment/submission period after
# an activation is a genuinely separate delay from the 1-hour gate,
# confirmed period-by-period, not folded into it).
_DC_ASSESSMENT_PERIOD = 1
_DC_RECOVERY_GATE_PERIODS = 2
_DC_RECOVERY_PERIODS_FROM_EMPTY = 5
_DC_MINIMUM_ENERGY_REQUIREMENT_HOURS = 0.25
_DC_ACTIVATION_RECOVERY_WINDOW_PERIODS = (
    _DC_ASSESSMENT_PERIOD + _DC_RECOVERY_GATE_PERIODS + _DC_RECOVERY_PERIODS_FROM_EMPTY
)


def _dc_recovery_weight(offset: int) -> float:
    """The fraction of a DC activation's minimum energy requirement
    still being drained (offset=0) or recovered (positive, during the
    confirmed recovery window) `offset` settlement periods after the
    activation itself. Sign convention here matches a discharge-type
    activation (DC-Low): -1.0 at offset=0 (the drain), then
    1/_DC_RECOVERY_PERIODS_FROM_EMPTY during each recovery period.
    Callers modelling a charge-type market (DC-High) negate this --
    see optimize_day()'s own usage, not repeated here.

    Confirmed period-by-period, not assumed: offsets 1 through
    (_DC_ASSESSMENT_PERIOD + _DC_RECOVERY_GATE_PERIODS) are genuinely
    idle (the assessment/submission period and the gate are two
    distinct delays, not one -- docs/adr/0027), recovery spans the
    _DC_RECOVERY_PERIODS_FROM_EMPTY periods after that, and nothing
    happens beyond the confirmed window (already fully recovered).
    """
    if offset == 0:
        return -1.0
    idle_end = _DC_ASSESSMENT_PERIOD + _DC_RECOVERY_GATE_PERIODS
    if offset <= idle_end:
        return 0.0
    recovery_end = idle_end + _DC_RECOVERY_PERIODS_FROM_EMPTY
    if offset <= recovery_end:
        return 1.0 / _DC_RECOVERY_PERIODS_FROM_EMPTY
    return 0.0


def _dc_activation_probability_or_default(prices: PriceProvider, day: date, market: Market) -> list[float]:
    """Optional PriceProvider extension: `dc_activation_probability(day, market) -> list[float]`.

    A genuinely different concept from BM's `acceptance_probability()`
    -- the probability of a real, SoE-depleting activation EVENT (a
    frequency excursion large enough to matter), not the probability
    of a bid being selected in an auction. Kept as a separate method
    name deliberately, not reused -- conflating the two would be
    semantically wrong even though both are checked the same
    structural way (hasattr(), not a formal PriceProvider Protocol
    member, same reasoning as _acceptance_probability_or_default()).

    Defaults to 0.0 -- no expected activation, hence no expected SoC
    impact, for any provider that hasn't opted in. Same safe-default
    philosophy as acceptance_probability: this preserves DC's
    pre-existing zero-SoC-impact behaviour exactly for any provider
    that doesn't implement it, rather than assuming a nonzero default
    for something that was never modelled that way before.
    """
    if not hasattr(prices, "dc_activation_probability"):
        return [0.0] * PERIODS_PER_DAY
    return prices.dc_activation_probability(day, market)


def _acceptance_probability_or_default(prices: PriceProvider, day: date, market: Market) -> list[float]:
    """Optional PriceProvider extension: `acceptance_probability(day, market) -> list[float]`.

    Deliberately NOT part of the formal PriceProvider Protocol
    (core/prices.py) -- adding a required method there would force
    every existing implementation (SyntheticPriceProvider, and every
    real provider that has no acceptance-risk concept, e.g. wholesale
    or DC's) to implement something that isn't meaningful for them.
    Checked structurally via hasattr(), the same duck-typing pattern
    already used throughout this project (e.g. market.name dispatch
    in the ingestion layer) rather than a formal interface.

    Defaults to 0.0, NOT 1.0, for any market/provider that doesn't
    implement it. This is the safe default deliberately: 0.0 means
    "no expected energy delivery from this commitment", which is
    exactly the pre-existing behaviour (reserve commitments had zero
    SoC impact before this capability existed) -- preserved exactly
    for any provider that hasn't opted in, rather than silently
    assuming "always accepted" for something that was never modelled
    that way.
    """
    if not hasattr(prices, "acceptance_probability"):
        return [0.0] * PERIODS_PER_DAY
    return prices.acceptance_probability(day, market)


def optimize_day(
    battery: BatterySpec,
    markets: list[Market],
    prices: PriceProvider,
    day: date,
    initial_soc_mwh: float | None = None,
    fixed_wholesale_mw: tuple[list[float], list[float]] | None = None,
) -> DispatchResult:
    """`fixed_wholesale_mw`, if given, is `(charge_mw, discharge_mw)`
    -- PERIODS_PER_DAY values each -- a wholesale schedule already
    decided elsewhere (stage 1 of a two-stage day-ahead process, see
    optimize_day_two_stage()), not something this call is free to
    choose. `Market.WHOLESALE` must NOT be in `markets` when this is
    given -- wholesale can't be both fixed and freely optimized in the
    same call, checked and rejected explicitly rather than silently
    picking one.

    Wholesale price is still fetched, and wholesale revenue still
    computed and reported, whenever a wholesale position exists at
    all (free OR fixed) -- the price is a real, known input either
    way, it's only the decision that differs. Adding a fixed
    schedule's revenue (a constant, given fixed charge/discharge and
    a known price) to the objective doesn't change where the optimum
    for the remaining free variables (reserve markets) sits -- it's
    mathematically harmless, included for a uniform code path rather
    than a special case.
    """
    dt = SETTLEMENT_PERIOD_HOURS
    periods = range(PERIODS_PER_DAY)

    if fixed_wholesale_mw is not None and Market.WHOLESALE in markets:
        raise ValueError(
            "wholesale cannot be both fixed (fixed_wholesale_mw given) and freely optimized "
            "(Market.WHOLESALE in markets) in the same call"
        )
    if fixed_wholesale_mw is not None:
        fixed_charge, fixed_discharge = fixed_wholesale_mw
        if len(fixed_charge) != PERIODS_PER_DAY or len(fixed_discharge) != PERIODS_PER_DAY:
            raise ValueError(
                f"fixed_wholesale_mw must have {PERIODS_PER_DAY} values in each list, "
                f"got {len(fixed_charge)} charge and {len(fixed_discharge)} discharge"
            )
        for t in periods:
            if not (0 <= fixed_charge[t] <= battery.power_mw):
                raise ValueError(
                    f"fixed_wholesale_mw charge at period {t} ({fixed_charge[t]}) outside [0, {battery.power_mw}]"
                )
            if not (0 <= fixed_discharge[t] <= battery.power_mw):
                raise ValueError(
                    f"fixed_wholesale_mw discharge at period {t} ({fixed_discharge[t]}) outside [0, {battery.power_mw}]"
                )

    active_reserve = [m for m in markets if m in MARKET_REGISTRY]
    wholesale_active = Market.WHOLESALE in markets
    wholesale_fixed = fixed_wholesale_mw is not None
    wholesale_known = wholesale_active or wholesale_fixed

    wholesale_price = prices.wholesale_prices(day) if wholesale_known else [0.0] * PERIODS_PER_DAY
    reserve_price = {m: prices.reserve_prices(day, m) for m in active_reserve}

    prob = pulp.LpProblem("wattstack_daily_dispatch", pulp.LpMaximize)

    if wholesale_fixed:
        charge = fixed_wholesale_mw[0]
        discharge = fixed_wholesale_mw[1]
    else:
        charge = pulp.LpVariable.dicts("charge", periods, lowBound=0, upBound=battery.power_mw)
        discharge = pulp.LpVariable.dicts("discharge", periods, lowBound=0, upBound=battery.power_mw)
    # A small, deliberately targeted tolerance -- only when wholesale is
    # fixed, not the general case. A fixed schedule is necessarily
    # already-rounded (DispatchResult.charge_mw/discharge_mw, 4 decimal
    # places) rather than the solver's own exact internal values; fed
    # back in as hard constants across 48 periods, that rounding can
    # accumulate into a SoC drift of a few thousandths of an MWh --
    # physically meaningless for any real battery, but enough to make
    # an LP with zero-tolerance hard bounds report infeasible. A freely
    # solved (non-fixed) SoC variable never has this problem, since the
    # solver finds an exact value rather than being handed a
    # pre-rounded one -- confirmed by every existing single-stage test
    # passing without this tolerance.
    soc_bound_tolerance = 1e-3 if wholesale_fixed else 0.0
    soc = pulp.LpVariable.dicts(
        "soc", periods,
        lowBound=battery.soc_min_mwh - soc_bound_tolerance,
        upBound=battery.soc_max_mwh + soc_bound_tolerance,
    )
    reserve = {
        m: pulp.LpVariable.dicts(f"reserve_{m.value}", periods, lowBound=0, upBound=battery.power_mw)
        for m in active_reserve
    }

    discharge_direction = [m for m in active_reserve if MARKET_REGISTRY[m].direction == "discharge"]
    charge_direction = [m for m in active_reserve if MARKET_REGISTRY[m].direction == "charge"]

    # Energy-settled ("per_mwh") reserve markets -- BM-Offer/BM-Bid --
    # have an expected energy impact tied to their OWN settlement unit
    # (paid per MWh actually delivered, so an accepted commitment
    # implies energy movement in that same period). Confirmed via
    # NESO's own 30-minute MEL/MIL rule (docs/adr/0023) that a called
    # BM commitment is genuinely deliverable for the full settlement
    # period, matching delivery_hours exactly -- not an approximation
    # of a shorter real dispatch window.
    energy_settled_discharge = [
        m for m in discharge_direction if MARKET_REGISTRY[m].settlement_unit == "per_mwh"
    ]
    energy_settled_charge = [
        m for m in charge_direction if MARKET_REGISTRY[m].settlement_unit == "per_mwh"
    ]
    acceptance_probability = {
        m: _acceptance_probability_or_default(prices, day, m)
        for m in energy_settled_discharge + energy_settled_charge
    }

    # Availability-settled ("per_mw_h") reserve markets -- DC-High/
    # DC-Low -- are paid regardless of whether they're actually
    # activated, but a genuine activation still has a real, physical
    # SoC impact: NESO's own State-of-Energy rules (docs/adr/0024,
    # 0027) mandate a specific multi-period drain-then-recover shape,
    # confirmed period-by-period, not the same-period expected-energy
    # shape BM uses. Filtered by settlement_unit, the same distinction
    # BM's own filter above already relies on -- genuinely
    # availability-settled markets, not "DC by name".
    dc_discharge = [m for m in discharge_direction if MARKET_REGISTRY[m].settlement_unit == "per_mw_h"]
    dc_charge = [m for m in charge_direction if MARKET_REGISTRY[m].settlement_unit == "per_mw_h"]
    dc_activation_probability = {
        m: _dc_activation_probability_or_default(prices, day, m)
        for m in dc_discharge + dc_charge
    }

    eff = battery.one_way_efficiency
    start_soc = (
        initial_soc_mwh if initial_soc_mwh is not None else (battery.soc_min_mwh + battery.soc_max_mwh) / 2
    )

    for t in periods:
        prev_soc = soc[t - 1] if t > 0 else start_soc

        # Expected energy actually delivered/absorbed by energy-settled
        # reserve commitments this period, weighted by acceptance
        # probability -- a deterministic-equivalent expectation, not a
        # scenario model. Zero whenever no provider has opted into
        # acceptance_probability (the default), leaving SoC dynamics
        # exactly as they were before this capability existed.
        expected_bm_discharge_mwh = pulp.lpSum(
            reserve[m][t] * MARKET_REGISTRY[m].delivery_hours * acceptance_probability[m][t]
            for m in energy_settled_discharge
        )
        expected_bm_charge_mwh = pulp.lpSum(
            reserve[m][t] * MARKET_REGISTRY[m].delivery_hours * acceptance_probability[m][t]
            for m in energy_settled_charge
        )

        # DC's expected SoC contribution this period -- NOT the same-
        # period shape BM uses above. A DC activation at some PRIOR
        # period t_prev has a confirmed, multi-period drain-then-
        # recover shape (docs/adr/0024, 0027): the full drain lands at
        # t_prev itself, then the idle assessment period and 1-hour
        # gate contribute nothing, then the recovery periods add back
        # 1/5 of the minimum energy requirement each. This period's
        # contribution sums every prior period within the confirmed
        # window that could still be affecting it. Sign convention:
        # DC-Low (discharge-direction) uses _dc_recovery_weight()
        # directly (drain=negative, recovery=positive, matching a
        # discharge-type event); DC-High (charge-direction) is the
        # mirror image (activation charges SoC up, recovery discharges
        # it back down), so its contribution is negated. Deliberately
        # NOT efficiency-scaled (no *eff or /eff) -- a stated
        # simplification, not an oversight: the minimum-energy
        # magnitude is already a conservative, worst-case estimate,
        # and splitting this multi-period, bidirectional term by
        # charge/discharge sign to apply efficiency correctly would add
        # real complexity for a second-order refinement.
        dc_expected_soc_contribution_mwh = pulp.lpSum(
            reserve[m][t_prev]
            * _DC_MINIMUM_ENERGY_REQUIREMENT_HOURS
            * dc_activation_probability[m][t_prev]
            * _dc_recovery_weight(t - t_prev)
            * (1.0 if m in dc_discharge else -1.0)
            for m in dc_discharge + dc_charge
            for t_prev in range(max(0, t - _DC_ACTIVATION_RECOVERY_WINDOW_PERIODS), t + 1)
        )

        prob += soc[t] == (
            prev_soc
            + charge[t] * dt * eff - discharge[t] * dt / eff
            + expected_bm_charge_mwh * eff - expected_bm_discharge_mwh / eff
            + dc_expected_soc_contribution_mwh
        )

        # Inverter can't simultaneously charge, discharge, and hold
        # reserve capacity (in any market, any direction) beyond its
        # rated power.
        prob += (
            charge[t] + discharge[t] + pulp.lpSum(reserve[m][t] for m in active_reserve)
            <= battery.power_mw
        )

        # Headroom: enough energy must be held back to deliver any
        # discharge-direction commitment for its full delivery
        # duration if called (DC-Low, BM-Offer). Unchanged by the
        # expected-energy term above -- this remains the worst-case
        # margin needed to sustain the FULL commitment if called,
        # regardless of how likely that call actually is.
        headroom = pulp.lpSum(reserve[m][t] * MARKET_REGISTRY[m].delivery_hours for m in discharge_direction)
        prob += soc[t] - headroom >= battery.soc_min_mwh - soc_bound_tolerance

        # Footroom: enough space must be held below the ceiling to
        # absorb any charge-direction commitment for its full delivery
        # duration if called (DC-High, BM-Bid).
        footroom = pulp.lpSum(reserve[m][t] * MARKET_REGISTRY[m].delivery_hours for m in charge_direction)
        prob += soc[t] + footroom <= battery.soc_max_mwh + soc_bound_tolerance

    objective_terms = []
    wholesale_revenue_expr = None
    if wholesale_known:
        wholesale_revenue_expr = pulp.lpSum((discharge[t] - charge[t]) * dt * wholesale_price[t] for t in periods)
        objective_terms.append(wholesale_revenue_expr)

    reserve_revenue_expr = {}
    for m in active_reserve:
        expr = pulp.lpSum(reserve[m][t] * dt * reserve_price[m][t] for t in periods)
        reserve_revenue_expr[m] = expr
        objective_terms.append(expr)

    prob += pulp.lpSum(objective_terms)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"Solver did not find an optimal solution: {pulp.LpStatus[prob.status]}")

    def val(x) -> float:
        return round(pulp.value(x) or 0.0, 4)

    revenue_by_market: dict[Market, float] = {m: 0.0 for m in Market}
    if wholesale_revenue_expr is not None:
        revenue_by_market[Market.WHOLESALE] = round(pulp.value(wholesale_revenue_expr) or 0.0, 2)
    for m, expr in reserve_revenue_expr.items():
        revenue_by_market[m] = round(pulp.value(expr) or 0.0, 2)

    return DispatchResult(
        day=day,
        soc_mwh=[val(soc[t]) for t in periods],
        charge_mw=[val(charge[t]) for t in periods],
        discharge_mw=[val(discharge[t]) for t in periods],
        reserve_mw={m: [val(reserve[m][t]) for t in periods] for m in active_reserve},
        revenue_by_market=revenue_by_market,
        wholesale_price=wholesale_price if wholesale_known else [],
        reserve_price=reserve_price,
        acceptance_probability=acceptance_probability,
        dc_activation_probability=dc_activation_probability,
    )


def optimize_day_two_stage(
    battery: BatterySpec,
    dc_markets: list[Market],
    stage1_prices: PriceProvider,
    stage2_prices: PriceProvider,
    day: date,
    initial_soc_mwh: float | None = None,
) -> TwoStageResult:
    """The real day-ahead decision sequence, not a simultaneous joint
    guess: wholesale gate closure (09:50) comes before DC/DM/DR/BR/QR
    gate closure (14:00), and by 14:00 the wholesale outcome is
    already known -- confirmed directly from NESO's own June 2026
    Balancing Reserve Guidance Document (docs/adr/0024). Two stages,
    two real decision points, not one LP pretending both happen at
    once.

    Stage 1 (~09:50): a joint wholesale + DC decision using
    `stage1_prices` for BOTH -- this is the only way to make an
    *informed* wholesale commitment, since blindly optimizing
    wholesale alone (ignoring DC's likely value) could commit capacity
    that would have been worth more held back for DC. Only the
    wholesale part of this decision is kept -- its own DC reserve_mw
    is a planning estimate, made before DC's real auction has cleared,
    and is discarded.

    Stage 2 (~14:00): DC decided fresh, using `stage2_prices` (whatever
    is most current by then -- may or may not differ from
    `stage1_prices`, that's the caller's choice), with the stage 1
    wholesale schedule passed in as `fixed_wholesale_mw` -- already
    committed, not re-optimized. `stage2_prices.wholesale_prices(day)`
    is still used for revenue reporting (the real, now-known price),
    even though it's no longer a decision.

    `Market.WHOLESALE` must not appear in `dc_markets` -- it's handled
    automatically (added for stage 1, fixed for stage 2), not something
    the caller passes in directly.
    """
    if Market.WHOLESALE in dc_markets:
        raise ValueError("dc_markets must not include Market.WHOLESALE -- it's handled automatically by both stages")

    stage1_markets = [Market.WHOLESALE, *dc_markets]
    stage1_plan = optimize_day(battery, stage1_markets, stage1_prices, day, initial_soc_mwh)

    fixed_wholesale_mw = (stage1_plan.charge_mw, stage1_plan.discharge_mw)
    stage2_final = optimize_day(
        battery, dc_markets, stage2_prices, day, initial_soc_mwh, fixed_wholesale_mw=fixed_wholesale_mw
    )

    return TwoStageResult(stage1_plan=stage1_plan, stage2_final=stage2_final)
