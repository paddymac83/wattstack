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

    active_reserve = [m for m in markets if m in MARKET_REGISTRY]
    wholesale_active = Market.WHOLESALE in markets

    wholesale_price = prices.wholesale_prices(day) if wholesale_active else [0.0] * PERIODS_PER_DAY
    reserve_price = {m: prices.reserve_prices(day, m) for m in active_reserve}

    prob = pulp.LpProblem("wattstack_daily_dispatch", pulp.LpMaximize)

    charge = pulp.LpVariable.dicts("charge", periods, lowBound=0, upBound=battery.power_mw)
    discharge = pulp.LpVariable.dicts("discharge", periods, lowBound=0, upBound=battery.power_mw)
    soc = pulp.LpVariable.dicts("soc", periods, lowBound=battery.soc_min_mwh, upBound=battery.soc_max_mwh)
    reserve = {
        m: pulp.LpVariable.dicts(f"reserve_{m.value}", periods, lowBound=0, upBound=battery.power_mw)
        for m in active_reserve
    }

    discharge_direction = [m for m in active_reserve if MARKET_REGISTRY[m].direction == "discharge"]
    charge_direction = [m for m in active_reserve if MARKET_REGISTRY[m].direction == "charge"]

    eff = battery.one_way_efficiency
    start_soc = (
        initial_soc_mwh if initial_soc_mwh is not None else (battery.soc_min_mwh + battery.soc_max_mwh) / 2
    )

    for t in periods:
        prev_soc = soc[t - 1] if t > 0 else start_soc

        prob += soc[t] == prev_soc + charge[t] * dt * eff - discharge[t] * dt / eff

        # Inverter can't simultaneously charge, discharge, and hold
        # reserve capacity (in any market, any direction) beyond its
        # rated power.
        prob += (
            charge[t] + discharge[t] + pulp.lpSum(reserve[m][t] for m in active_reserve)
            <= battery.power_mw
        )

        # Headroom: enough energy must be held back to deliver any
        # discharge-direction commitment for its full delivery
        # duration if called (DC-Low, BM-Offer).
        headroom = pulp.lpSum(reserve[m][t] * MARKET_REGISTRY[m].delivery_hours for m in discharge_direction)
        prob += soc[t] - headroom >= battery.soc_min_mwh

        # Footroom: enough space must be held below the ceiling to
        # absorb any charge-direction commitment for its full delivery
        # duration if called (DC-High, BM-Bid).
        footroom = pulp.lpSum(reserve[m][t] * MARKET_REGISTRY[m].delivery_hours for m in charge_direction)
        prob += soc[t] + footroom <= battery.soc_max_mwh

    objective_terms = []
    wholesale_revenue_expr = None
    if wholesale_active:
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
        wholesale_price=wholesale_price if wholesale_active else [],
        reserve_price=reserve_price,
    )
