"""User-facing scenario configuration.

A Scenario is the one object that fully describes a run: the asset,
which markets to include, the backtest window, and an optional
parameter sweep. The same Scenario is built from a YAML file for the
CLI, or from Django form data for the web UI -- the optimizer and
backtest engine never know or care which produced it.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, ValidationInfo

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market


class BacktestWindow(BaseModel):
    start: date
    end: date
    foresight: str = Field("rolling", pattern="^(rolling|perfect)$")

    @field_validator("end")
    @classmethod
    def end_after_start(cls, v: date, info: ValidationInfo) -> date:
        start = info.data.get("start")
        if start and v < start:
            raise ValueError("backtest.end must not be before backtest.start")
        return v


class Scenario(BaseModel):
    name: str = "untitled-scenario"
    battery: BatterySpec
    markets: list[Market] = Field(default_factory=lambda: list(Market))
    backtest: BacktestWindow
    sweep: dict[str, list[float]] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Scenario":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        with open(path, "w") as f:
            yaml.safe_dump(self.model_dump(mode="json"), f, sort_keys=False)
