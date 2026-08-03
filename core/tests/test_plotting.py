from datetime import date

import pytest

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market
from wattstack_core.optimizer import optimize_day
from wattstack_core.prices import SyntheticPriceProvider

plotly = pytest.importorskip("plotly")
from wattstack_core.plotting import dispatch_figure  # noqa: E402


def _sample_result(markets=None):
    battery = BatterySpec(power_mw=5, duration_hours=2)
    markets = markets or [Market.ENERGY, Market.DYNAMIC_CONTAINMENT, Market.DYNAMIC_REGULATION]
    result = optimize_day(battery, markets, SyntheticPriceProvider(), date(2025, 1, 1))
    return battery, result


def test_dispatch_figure_builds_without_error():
    battery, result = _sample_result()
    fig = dispatch_figure(result, battery)
    assert fig is not None


def test_dispatch_figure_has_one_trace_per_active_response_market():
    battery, result = _sample_result()
    fig = dispatch_figure(result, battery)
    names = {t.name for t in fig.data}
    assert "Dynamic Containment" in names
    assert "Dynamic Regulation" in names


def test_dispatch_figure_includes_price_traces_when_prices_present():
    battery, result = _sample_result()
    fig = dispatch_figure(result, battery)
    assert any("price" in (t.name or "").lower() for t in fig.data)


def test_dispatch_figure_drops_price_panel_when_no_prices_recorded():
    battery, result = _sample_result(markets=[Market.ENERGY])
    result.response_price = {}
    result.energy_price = []
    fig = dispatch_figure(result, battery)
    assert not any("price" in (t.name or "").lower() for t in fig.data)
    # 4 subplot titles instead of 5
    assert len(fig.layout.annotations) == 4


def test_headroom_and_footroom_sum_to_usable_energy_range():
    battery, result = _sample_result()
    fig = dispatch_figure(result, battery)
    headroom = next(t for t in fig.data if t.name == "Headroom (can discharge)").y
    footroom = next(t for t in fig.data if t.name == "Footroom (can charge)").y
    usable_range = battery.soc_max_mwh - battery.soc_min_mwh
    for h, f in zip(headroom, footroom):
        assert abs((h + f) - usable_range) < 1e-6
