"""The actual script: `wattstack-explore`.

Fetches real Elexon (and, where configured, NESO) data, verifies
neither API has silently changed shape, and writes a set of
exploratory HTML charts to disk. Deliberately a script, not a
notebook -- reproducible, diffable, runnable in CI, and it's the
thing you re-run after any API change rather than a stateful session
you have to remember to re-execute top to bottom.

Usage:
    wattstack-explore --days 14
    wattstack-explore --days 30 --out reports/ --embed-js
    wattstack-explore --verify-only
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import click

from wattstack_ingestion.cache import Cache
from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.neso import NesoClient
from wattstack_ingestion.plots import (
    price_by_hour_of_day,
    price_by_weekday,
    price_distribution,
    price_timeseries,
)

# NESO_PRICE_FIELD: fill this in once you've run with --verify-only
# and seen the real field names printed. NESO_DATE_FIELD is set from
# a confirmed source (NESO's own EAC results page states the dataset
# uses deliveryStart/deliveryEnd, UTC) -- still worth double-checking
# against verify_schema()'s output on first run, not blindly trusted.
NESO_DATE_FIELD: str | None = "deliveryStart"
NESO_PRICE_FIELD: str | None = None


def _period_to_datetime(settlement_date: str, settlement_period: int) -> datetime:
    d = date.fromisoformat(settlement_date[:10])
    return datetime(d.year, d.month, d.day) + timedelta(minutes=30 * (settlement_period - 1))


def _save(fig, out_dir: Path, name: str, embed_js: bool) -> None:
    path = out_dir / f"{name}.html"
    fig.write_html(path, include_plotlyjs=True if embed_js else "cdn")
    click.echo(f"  wrote {path}")


@click.command()
@click.option("--days", default=14, help="How many past days of Elexon system prices to fetch")
@click.option("--out", "out_dir", default="reports", type=click.Path(path_type=Path), help="Output directory for charts")
@click.option("--cache-path", default="wattstack_ingestion_cache.sqlite", help="SQLite cache file")
@click.option("--embed-js", is_flag=True, help="Embed Plotly.js inline instead of CDN -- use if your browser/viewer blocks external scripts")
@click.option("--verify-only", is_flag=True, help="Just check both APIs are reachable and print their schemas, then exit")
def main(days: int, out_dir: Path, cache_path: str, embed_js: bool, verify_only: bool) -> None:
    cache = Cache(cache_path)
    elexon = ElexonClient(cache=cache)
    neso = NesoClient(cache=cache)

    click.echo("Verifying Elexon system-prices schema...")
    sample_day = date.today() - timedelta(days=2)  # avoid today/yesterday, which may not be published yet
    elexon_fields = elexon.verify_schema(sample_day)
    click.echo(f"  OK -- fields: {sorted(elexon_fields)}")

    click.echo("Verifying NESO datastore is reachable...")
    neso_fields = neso.verify_schema()
    click.echo(f"  OK -- fields: {sorted(neso_fields)}")
    if NESO_DATE_FIELD is None or NESO_PRICE_FIELD is None:
        missing = [
            name for name, val in [("NESO_DATE_FIELD", NESO_DATE_FIELD), ("NESO_PRICE_FIELD", NESO_PRICE_FIELD)]
            if val is None
        ]
        click.echo(
            f"  {' and '.join(missing)} not set in cli.py yet -- "
            "look at the field list above, decide which column(s) you actually want, "
            "and fill them in. Skipping NESO plots until then."
        )

    if verify_only:
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"Fetching {days} days of Elexon system prices (cached after first run)...")
    timestamps: list[datetime] = []
    sell_prices: list[float] = []
    for offset in range(days, 0, -1):
        day = date.today() - timedelta(days=offset + 1)  # stay clear of not-yet-published days
        for row in elexon.system_prices(day):
            timestamps.append(_period_to_datetime(row["settlementDate"], row["settlementPeriod"]))
            sell_prices.append(row["systemSellPrice"])

    if not timestamps:
        click.echo("No Elexon data fetched -- nothing to plot.")
        return

    click.echo(f"Got {len(timestamps)} settlement periods. Building charts...")
    _save(
        price_timeseries(timestamps, sell_prices, f"GB system sell price, last {days} days", "GBP/MWh"),
        out_dir, "elexon_system_price_timeseries", embed_js,
    )
    _save(
        price_distribution(sell_prices, f"GB system sell price distribution, last {days} days", "GBP/MWh"),
        out_dir, "elexon_system_price_distribution", embed_js,
    )
    _save(
        price_by_hour_of_day(timestamps, sell_prices, "System sell price by hour of day", "GBP/MWh"),
        out_dir, "elexon_system_price_by_hour", embed_js,
    )
    _save(
        price_by_weekday(timestamps, sell_prices, "System sell price by day of week", "GBP/MWh"),
        out_dir, "elexon_system_price_by_weekday", embed_js,
    )

    if NESO_DATE_FIELD and NESO_PRICE_FIELD:
        click.echo("Building NESO chart...")
        records = neso.response_reserve_results_summary()
        neso_ts = [datetime.fromisoformat(r[NESO_DATE_FIELD]) for r in records]
        neso_vals = [r[NESO_PRICE_FIELD] for r in records]
        _save(
            price_timeseries(neso_ts, neso_vals, "NESO Response-Reserve results (current)", NESO_PRICE_FIELD),
            out_dir, "neso_price_timeseries", embed_js,
        )

    click.echo(f"Done. Open the files in {out_dir}/ in a browser.")


if __name__ == "__main__":
    main()
