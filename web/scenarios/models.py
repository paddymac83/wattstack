"""Persistence for scenarios and the backtests run against them.

The point isn't to duplicate wattstack_core's logic in the database --
it's so a run can be saved, revisited, and compared later, and so a
saved config is a reproducible record of how a revenue number was
produced. Anyone can be handed a ScenarioRecord's config and rerun it.
"""
from django.db import models


class ScenarioRecord(models.Model):
    name = models.CharField(max_length=200)
    config = models.JSONField(help_text="Serialised wattstack_core.scenario.Scenario")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name


class BacktestRunRecord(models.Model):
    scenario = models.ForeignKey(ScenarioRecord, on_delete=models.CASCADE, related_name="runs")
    revenue_by_market = models.JSONField()
    total_revenue = models.FloatField()
    revenue_per_mw_year = models.FloatField()
    ran_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ran_at"]

    def __str__(self) -> str:
        return f"{self.scenario.name} @ {self.ran_at:%Y-%m-%d %H:%M}"
