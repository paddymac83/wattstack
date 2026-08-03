"""The core LP: one day's optimal dispatch across energy and response
markets, with state-of-charge headroom reserved for any response
commitment that period is exposed to.

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
from wattstack_core.markets import (
    RESPONSE_DELIVERY_HOURS,
    RESPONSE_MARKETS,
    SETTLEMENT_PERIOD_HOURS,
    Market,
)
from wattstack_core.prices import PERIODS_PER_DAY, PriceProvider
from wattstack_core.results import DispatchResult


def optimize_day(
    battery: BatterySpec,
    markets: list[Market],
    prices: PriceProvider,
    day: date,
    initial_soc_mwh: float | None = None,
) -> DispatchResult:
    dt = SETTLEMENT_PERIOD_HOURS
    periods = range(PERIODS_PER_DAY)

    active_response = [m for m in RESPONSE_MARKETS if m in markets]
    energy_active = Market.ENERGY in markets
    energy_price = prices.energy_prices(day) if energy_active else [0.0] * PERIODS_PER_DAY
    response_price = {m: prices.response_prices(day, m) for m in active_response}

    prob = pulp.LpProblem("wattstack_daily_dispatch", pulp.LpMaximize)

    charge = pulp.LpVariable.dicts("charge", periods, lowBound=0, upBound=battery.power_mw)
    discharge = pulp.LpVariable.dicts("discharge", periods, lowBound=0, upBound=battery.power_mw)
    soc = pulp.LpVariable.dicts("soc", periods, lowBound=battery.soc_min_mwh, upBound=battery.soc_max_mwh)
    reserve = {
        m: pulp.LpVariable.dicts(f"reserve_{m.value}", periods, lowBound=0, upBound=battery.power_mw)
        for m in active_response
    }

    eff = battery.one_way_efficiency
    start_soc = (
        initial_soc_mwh if initial_soc_mwh is not None else (battery.soc_min_mwh + battery.soc_max_mwh) / 2
    )

    for t in periods:
        prev_soc = soc[t - 1] if t > 0 else start_soc

        prob += soc[t] == prev_soc + charge[t] * dt * eff - discharge[t] * dt / eff

        # Inverter can't simultaneously charge, discharge, and hold
        # response capacity beyond its rated power.
        prob += (
            charge[t] + discharge[t] + pulp.lpSum(reserve[m][t] for m in active_response)
            <= battery.power_mw
        )

        # Headroom: enough energy must be held back to deliver any
        # committed response for its full delivery duration if called.
        headroom = pulp.lpSum(reserve[m][t] * RESPONSE_DELIVERY_HOURS[m] for m in active_response)
        prob += soc[t] - headroom >= battery.soc_min_mwh

    objective_terms = []
    energy_revenue_expr = None
    if energy_active:
        energy_revenue_expr = pulp.lpSum((discharge[t] - charge[t]) * dt * energy_price[t] for t in periods)
        objective_terms.append(energy_revenue_expr)

    response_revenue_expr = {}
    for m in active_response:
        expr = pulp.lpSum(reserve[m][t] * dt * response_price[m][t] for t in periods)
        response_revenue_expr[m] = expr
        objective_terms.append(expr)

    prob += pulp.lpSum(objective_terms)
    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"Solver did not find an optimal solution: {pulp.LpStatus[prob.status]}")

    def val(x) -> float:
        return round(pulp.value(x) or 0.0, 4)

    revenue_by_market: dict[Market, float] = {m: 0.0 for m in Market}
    if energy_revenue_expr is not None:
        revenue_by_market[Market.ENERGY] = round(pulp.value(energy_revenue_expr) or 0.0, 2)
    for m, expr in response_revenue_expr.items():
        revenue_by_market[m] = round(pulp.value(expr) or 0.0, 2)

    return DispatchResult(
        day=day,
        soc_mwh=[val(soc[t]) for t in periods],
        charge_mw=[val(charge[t]) for t in periods],
        discharge_mw=[val(discharge[t]) for t in periods],
        reserve_mw={m: [val(reserve[m][t]) for t in periods] for m in active_response},
        revenue_by_market=revenue_by_market,
        energy_price=energy_price if energy_active else [],
        response_price=response_price,
    )
