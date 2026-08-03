"""Result types returned by the optimizer and backtest engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from wattstack_core.markets import Market


@dataclass
class DispatchResult:
    """One day's optimal dispatch."""

    day: date
    soc_mwh: list[float]
    charge_mw: list[float]
    discharge_mw: list[float]
    reserve_mw: dict[Market, list[float]]
    revenue_by_market: dict[Market, float]
    # The prices actually used to produce this dispatch -- carried
    # alongside the result so downstream consumers (plotting, in
    # particular) don't need a PriceProvider or risk recomputing
    # something different from what the optimizer actually saw.
    energy_price: list[float] = field(default_factory=list)
    response_price: dict[Market, list[float]] = field(default_factory=dict)

    @property
    def total_revenue(self) -> float:
        return sum(self.revenue_by_market.values())


@dataclass
class BacktestResult:
    """Aggregated result across a backtest window."""

    scenario_name: str
    days: list[DispatchResult] = field(default_factory=list)

    @property
    def revenue_by_market(self) -> dict[Market, float]:
        totals: dict[Market, float] = {}
        for d in self.days:
            for market, value in d.revenue_by_market.items():
                totals[market] = totals.get(market, 0.0) + value
        return totals

    @property
    def total_revenue(self) -> float:
        return sum(self.revenue_by_market.values())

    def revenue_per_mw_year(self, power_mw: float) -> float:
        n_days = max(len(self.days), 1)
        annualised = self.total_revenue * (365 / n_days)
        return annualised / power_mw
