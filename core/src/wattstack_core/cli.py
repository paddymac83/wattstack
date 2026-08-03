"""Command-line interface: `wattstack run --config scenario.yaml`."""
from __future__ import annotations

import click

from wattstack_core.backtest import run_backtest, run_sweep
from wattstack_core.scenario import Scenario


@click.group()
def main() -> None:
    """wattstack: GB battery revenue-stacking backtests."""


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--sweep", "do_sweep", is_flag=True, help="Run the scenario's sweep block instead of a single backtest")
def run(config_path: str, do_sweep: bool) -> None:
    """Run a scenario and print a revenue summary."""
    scenario = Scenario.from_yaml(config_path)

    if do_sweep:
        results = run_sweep(scenario)
        click.echo(f"Sweep: {scenario.name} (duration_hours)")
        for duration, result in sorted(results.items()):
            per_mw = result.revenue_per_mw_year(scenario.battery.power_mw)
            click.echo(f"  duration={duration:>4}h  revenue=GBP {per_mw:,.0f}/MW/yr")
        return

    result = run_backtest(scenario)
    per_mw = result.revenue_per_mw_year(scenario.battery.power_mw)
    click.echo(f"Scenario: {result.scenario_name}")
    click.echo(f"Revenue: GBP {per_mw:,.0f}/MW/yr  (GBP {result.total_revenue:,.0f} total over {len(result.days)} days)")
    click.echo("Breakdown:")
    for market, value in result.revenue_by_market.items():
        if value:
            click.echo(f"  {market.value}: GBP {value:,.0f}")


if __name__ == "__main__":
    main()
