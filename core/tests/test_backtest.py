from datetime import date

from wattstack_core.backtest import run_backtest, run_sweep
from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.scenario import BacktestWindow, Scenario


def make_scenario(duration_hours=2, sweep=None):
    return Scenario(
        name="test",
        battery=BatterySpec(power_mw=5, duration_hours=duration_hours),
        markets=[Market.ENERGY, Market.DYNAMIC_CONTAINMENT],
        backtest=BacktestWindow(start=date(2025, 1, 1), end=date(2025, 1, 3)),
        sweep=sweep or {},
    )


def test_backtest_runs_one_day_per_calendar_day():
    result = run_backtest(make_scenario())
    assert len(result.days) == 3


def test_backtest_produces_full_periods_each_day():
    result = run_backtest(make_scenario())
    assert all(len(d.soc_mwh) == 48 for d in result.days)


def test_sweep_runs_once_per_duration_value():
    scenario = make_scenario(sweep={"duration_hours": [1, 2, 4]})
    results = run_sweep(scenario)
    assert set(results.keys()) == {1, 2, 4}


def test_longer_duration_generally_earns_more_total_revenue():
    scenario = make_scenario(sweep={"duration_hours": [1, 4]})
    results = run_sweep(scenario)
    assert results[4].total_revenue >= results[1].total_revenue - 1e-6


def test_sweep_without_duration_key_raises():
    import pytest

    scenario = make_scenario(sweep={"efficiency": [0.8, 0.9]})
    with pytest.raises(ValueError):
        run_sweep(scenario)
