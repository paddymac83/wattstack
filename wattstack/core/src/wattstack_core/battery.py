"""Physical model of a battery energy storage system."""
from __future__ import annotations

from pydantic import BaseModel, Field, computed_field


class BatterySpec(BaseModel):
    """The physical capabilities of a battery asset.

    Everything downstream (the optimizer, the backtest engine, the UI)
    treats this as the single source of truth for what the asset can
    physically do. Nothing here is market-specific -- that separation
    is deliberate, see docs/adr.
    """

    power_mw: float = Field(..., gt=0, description="Inverter power rating, MW")
    duration_hours: float = Field(..., gt=0, description="Energy capacity expressed as hours at full power")
    round_trip_efficiency: float = Field(0.88, gt=0, le=1, description="AC-to-AC round trip efficiency")
    soc_min_pct: float = Field(0.05, ge=0, lt=1, description="Minimum usable state of charge, as a fraction")
    soc_max_pct: float = Field(0.95, gt=0, le=1, description="Maximum usable state of charge, as a fraction")

    @computed_field
    @property
    def energy_mwh(self) -> float:
        return self.power_mw * self.duration_hours

    @computed_field
    @property
    def one_way_efficiency(self) -> float:
        """Split round-trip efficiency evenly across charge and discharge."""
        return self.round_trip_efficiency**0.5

    @property
    def soc_min_mwh(self) -> float:
        return self.soc_min_pct * self.energy_mwh

    @property
    def soc_max_mwh(self) -> float:
        return self.soc_max_pct * self.energy_mwh
