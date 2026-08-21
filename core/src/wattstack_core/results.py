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
    wholesale_price: list[float] = field(default_factory=list)
    reserve_price: dict[Market, list[float]] = field(default_factory=dict)
    # The acceptance probability actually used for each energy-settled
    # ("per_mwh") reserve market's expected SoC impact -- only ever
    # has entries for BM markets, never DC, which has its own,
    # differently-shaped dc_activation_probability field below instead
    # (see optimizer.py). A market's entry is present whenever that
    # market is active, regardless of whether its PriceProvider
    # implements the optional acceptance_probability() extension -- it
    # defaults to an all-zero list when it doesn't (the safe backward-
    # compatible default). So check the actual values, not just key
    # presence, to know whether real acceptance-risk modelling was
    # used for a given market.
    acceptance_probability: dict[Market, list[float]] = field(default_factory=dict)
    # The activation probability actually used for each DC market's
    # expected SoC impact -- only ever has entries for DC-High/DC-Low,
    # never BM (which uses acceptance_probability above instead). A
    # genuinely different concept from acceptance_probability: this is
    # the probability of a real, SoE-depleting activation event, not
    # of a bid being selected in an auction -- kept as a separate
    # field deliberately, not merged, since conflating the two would
    # misrepresent what each one actually measures. Same safe-default
    # behaviour: defaults to an all-zero list for any provider that
    # hasn't implemented the optional dc_activation_probability()
    # extension.
    dc_activation_probability: dict[Market, list[float]] = field(default_factory=dict)

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


@dataclass
class TwoStageResult:
    """The output of optimize_day_two_stage() -- two real,
    chronologically distinct decisions, not two views of the same
    answer. Field names are deliberately not generic ("first"/
    "second") specifically to make misuse harder: `stage1_plan`'s own
    DC reserve_mw is a planning estimate only, made before DC's real
    auction has cleared, and is discarded -- never the commitment
    actually acted on. `stage2_final` is the real answer: wholesale
    fixed exactly as stage1_plan decided it (already committed,
    unchangeable by this point), DC decided fresh with stage2's own
    pricing. Anything downstream (reporting, execution, revenue
    totals) should use stage2_final, not stage1_plan.
    """

    stage1_plan: DispatchResult
    stage2_final: DispatchResult
