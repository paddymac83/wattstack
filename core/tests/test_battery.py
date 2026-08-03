from wattstack_core.battery import BatterySpec


def test_energy_mwh_is_power_times_duration():
    b = BatterySpec(power_mw=10, duration_hours=2)
    assert b.energy_mwh == 20


def test_soc_bounds_scale_with_energy():
    b = BatterySpec(power_mw=10, duration_hours=2, soc_min_pct=0.1, soc_max_pct=0.9)
    assert b.soc_min_mwh == 2
    assert b.soc_max_mwh == 18


def test_one_way_efficiency_is_sqrt_of_round_trip():
    b = BatterySpec(power_mw=10, duration_hours=2, round_trip_efficiency=0.81)
    assert abs(b.one_way_efficiency - 0.9) < 1e-9


def test_zero_power_battery_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BatterySpec(power_mw=0, duration_hours=2)
