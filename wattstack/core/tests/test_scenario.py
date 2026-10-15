from datetime import date

import pytest

from wattstack_core.markets import Market
from wattstack_core.scenario import Scenario


def make_scenario(**overrides):
    data = dict(
        name="test",
        battery=dict(power_mw=5, duration_hours=2),
        markets=[Market.WHOLESALE],
        backtest=dict(start=date(2025, 1, 1), end=date(2025, 1, 2)),
    )
    data.update(overrides)
    return Scenario.model_validate(data)


def test_scenario_round_trips_through_yaml(tmp_path):
    scenario = make_scenario()
    path = tmp_path / "scenario.yaml"
    scenario.to_yaml(path)
    loaded = Scenario.from_yaml(path)
    assert loaded.battery.power_mw == scenario.battery.power_mw
    assert loaded.markets == scenario.markets


def test_backtest_end_before_start_is_rejected():
    with pytest.raises(ValueError):
        make_scenario(backtest=dict(start=date(2025, 1, 5), end=date(2025, 1, 1)))
