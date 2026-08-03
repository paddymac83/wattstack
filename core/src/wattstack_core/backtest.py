"""Multi-day backtest: solve each day's dispatch independently and
aggregate the results.

Solving each day independently means every day gets that day's actual
prices with full knowledge of it -- effectively a perfect-foresight
backtest at the day level, regardless of the `foresight` field on
BacktestWindow. A true rolling/realistic mode (deciding today's
day-ahead bid without seeing today's real-time outturn) is real future
work, not yet implemented. See docs/adr/0002.
"""
from __future__ import annotations

from datetime import timedelta

from wattstack_core.optimizer import optimize_day
from wattstack_core.prices import PriceProvider, SyntheticPriceProvider
from wattstack_core.results import BacktestResult
from wattstack_core.scenario import Scenario


def run_backtest(scenario: Scenario, prices: PriceProvider | None = None) -> BacktestResult:
    prices = prices or SyntheticPriceProvider()
    result = BacktestResult(scenario_name=scenario.name)

    day = scenario.backtest.start
    soc_carry: float | None = None
    while day <= scenario.backtest.end:
        dispatch = optimize_day(
            battery=scenario.battery,
            markets=scenario.markets,
            prices=prices,
            day=day,
            initial_soc_mwh=soc_carry,
        )
        soc_carry = dispatch.soc_mwh[-1]
        result.days.append(dispatch)
        day += timedelta(days=1)

    return result


def run_sweep(scenario: Scenario, prices: PriceProvider | None = None) -> dict[float, BacktestResult]:
    """Re-run the backtest once per value in scenario.sweep["duration_hours"].

    Currently only a duration sweep is supported -- the one dimension
    this whole project cares most about. Extend here as more sweep
    axes (efficiency, market on/off combinations, ...) are needed.
    """
    if "duration_hours" not in scenario.sweep:
        raise ValueError("Only a duration_hours sweep is currently supported")

    results: dict[float, BacktestResult] = {}
    for duration in scenario.sweep["duration_hours"]:
        swept_battery = scenario.battery.model_copy(update={"duration_hours": duration})
        swept_scenario = scenario.model_copy(update={"battery": swept_battery})
        results[duration] = run_backtest(swept_scenario, prices)
    return results
