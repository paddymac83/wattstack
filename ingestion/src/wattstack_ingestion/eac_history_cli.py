"""`wattstack-eac-history`: download, inspect and backtest historical EAC auctions.

  download   pull a delivery-day range from the NESO data portal into CSVs
  inspect    load + validate + census + reconciliation; NO solving (fast)
  run        clear the day's auctions with wattstack and compare with NESO
  synth      write a SYNTHETIC day in the same layout, for smoke tests
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import click

from wattstack_ingestion.eac_auction import OverholdingMode
from wattstack_ingestion.eac_history import (
    HistoryDataError,
    build_case,
    day_bounds_utc,
    load_history_dir,
)
from wattstack_ingestion.eac_history_compare import (
    Tolerances,
    check_actual,
    actual_outcome,
    reconcile,
    run_day,
    write_outputs,
)
from wattstack_ingestion.neso import NesoClient

_DAY = click.DateTime(formats=["%Y-%m-%d"])


def _echo(msg: str) -> None:
    click.echo(msg, err=True)


@click.group()
def main() -> None:
    """Backtest wattstack's EAC clearing against NESO's published auctions."""


@main.command()
@click.option("--start", "start", type=_DAY, required=True, help="First delivery day (YYYY-MM-DD). Results in the new format start 2026-05-06.")
@click.option("--end", "end", type=_DAY, default=None, help="Last delivery day, inclusive (default: same as --start).")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), required=True)
@click.option("--basis", type=click.Choice(["efa", "calendar"]), default="efa", show_default=True)
@click.option("--page-size", type=int, default=5000, show_default=True)
def download(start, end, out_dir: Path, basis: str, page_size: int) -> None:
    """Download whole auctions delivering in the date range into OUT/*.csv."""
    from wattstack_ingestion.eac_history import download_range

    end = end or start
    try:
        manifest = download_range(NesoClient(timeout=120), start.date(), end.date(), out_dir, basis=basis,
                                  page_size=page_size, log=_echo)
    except HistoryDataError as exc:
        raise click.ClickException(str(exc))
    click.echo(f"Wrote {out_dir}: rows {manifest['rows']}, auctions {manifest['auction_ids']}")


def _load(data_dir: Path, since=None):
    try:
        return load_history_dir(data_dir, since=since.date() if since else None)
    except HistoryDataError as exc:
        raise click.ClickException(str(exc))


@main.command()
@click.option("--data", "data_dir", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--day", type=_DAY, required=True, help="Delivery day, YYYY-MM-DD.")
@click.option("--basis", type=click.Choice(["efa", "calendar"]), default="efa", show_default=True)
@click.option("--auction-id", "auction_ids", type=int, multiple=True)
@click.option("--excluded", type=click.Choice(["drop", "keep"]), default="drop", show_default=True)
@click.option("--products", default=None, help="Comma list, smoke tests only (distorts the market).")
@click.option("--since", type=_DAY, default="2026-05-06", show_default=True,
              help="Skip CSV rows delivering before this UTC date (the new format starts 2026-05-06).")
def inspect(data_dir, day, basis, auction_ids, excluded, products, since) -> None:
    """Validate the data and show what is in it, without solving anything."""
    data = _load(data_dir, since)
    window = day_bounds_utc(day.date(), basis)
    ids = list(auction_ids) or data.auction_ids_in(window)
    click.echo(f"Delivery window (UTC): {window[0]} .. {window[1]}; auctions: {ids or 'NONE FOUND'}")
    for auction_id in ids:
        case = build_case(auction_id, data, products=products.split(",") if products else None, excluded=excluded)
        ce = case.census
        click.echo(f"\nAuction {auction_id}: {ce.n_sell_rows} sell rows {dict(ce.sell_status)}, "
                   f"{ce.n_buy_rows} buy rows {dict(ce.buy_status)}")
        click.echo(f"  products (windows): {ce.products}")
        click.echo(f"  sub-markets: {ce.n_components}; largest {ce.largest_component_orders} orders / "
                   f"{ce.largest_component_keys} product-windows  <- the size of the biggest MILP")
        click.echo(f"  looped baskets {ce.n_looped_baskets}, buy families {ce.n_buy_families}, order types {dict(ce.order_types)}")
        if ce.unmodelled:
            click.echo(f"  NOT MODELLED but present: {ce.unmodelled}")
        if ce.reason_codes:
            click.echo(f"  reason codes: {dict(ce.reason_codes.most_common(8))}")
        for note in ce.window_length_anomalies:
            click.echo(f"  window length: {note}")
        for issue in case.issues:
            click.echo(f"  DATA ISSUE {issue.kind}: {issue.detail}")
        findings = reconcile(case, data.units, Tolerances())
        click.echo(f"  reconciliation: {'all checks passed' if not findings else len(findings)} "
                   + ("" if not findings else str({k: sum(1 for f in findings if f.kind == k) for k in {f.kind for f in findings}})))
        rules = check_actual(case, actual_outcome(case), Tolerances().rule_check)
        click.echo(f"  real outcome vs wattstack rules: {'no violations' if not rules.violations else dict(rules.by_category)}")


@main.command()
@click.option("--data", "data_dir", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--day", type=_DAY, required=True, help="Delivery day, YYYY-MM-DD.")
@click.option("--out", "out_dir", type=click.Path(path_type=Path), required=True)
@click.option("--basis", type=click.Choice(["efa", "calendar"]), default="efa", show_default=True)
@click.option("--mode", type=click.Choice(["restricted", "permissive", "both"]), default="restricted", show_default=True,
              help="Overholding mode (design report Section 5.3).")
@click.option("--auction-id", "auction_ids", type=int, multiple=True, help="Only these auctions (repeatable).")
@click.option("--products", default=None, help="Comma list, e.g. DCL,DCH. SMOKE TEST ONLY: distorts the market.")
@click.option("--excluded", type=click.Choice(["drop", "keep"]), default="drop", show_default=True)
@click.option("--time-limit", "time_limit", type=float, default=None, help="CBC seconds PER sub-market.")
@click.option("--mip-gap", type=float, default=None, help="Relative MIP gap (default: prove optimality).")
@click.option("--price-bounds", nargs=2, type=float, default=None, help="LO HI market price limits (Section 7.3).")
@click.option("--strict", is_flag=True, help="Fail on data issues instead of quarantining them.")
@click.option("--since", type=_DAY, default="2026-05-06", show_default=True,
              help="Skip CSV rows delivering before this UTC date (the new format starts 2026-05-06).")
def run(data_dir, day, out_dir, basis, mode, auction_ids, products, excluded, time_limit, mip_gap, price_bounds, strict, since) -> None:
    """Clear the day's auctions and write the comparison to OUT/."""
    data = _load(data_dir, since)
    modes = (OverholdingMode.RESTRICTED, OverholdingMode.PERMISSIVE) if mode == "both" else (OverholdingMode(mode),)
    try:
        result = run_day(
            data, day.date(), basis=basis, modes=modes, auction_ids=list(auction_ids) or None,
            products=products.split(",") if products else None, excluded=excluded,
            price_bounds=tuple(price_bounds) if price_bounds else None, time_limit_s=time_limit, mip_gap=mip_gap,
            strict=strict, log=_echo,
        )
    except (ValueError, HistoryDataError) as exc:
        raise click.ClickException(str(exc))
    report = write_outputs(result, out_dir)
    for c in result.comparisons:
        s = c.stats
        if c.sim.result is None:
            click.echo(f"auction {s['auction_id']} [{c.sim.mode.value}]: FAILED -- {c.sim.error}")
            continue
        click.echo(
            f"auction {s['auction_id']} [{c.sim.mode.value}]: welfare actual {s['welfare_actual']:,.0f} vs wattstack "
            f"{s['welfare_sim']:,.0f} (gap {s['welfare_gap']:+,.0f}); windows {s['windows']}, price match "
            f"{(s['price_match_rate'] or 0) * 100:.1f}%, volume match {(s['volume_match_rate'] or 0) * 100:.1f}%"
        )
    click.echo(f"\nReport: {report}")


@main.command()
@click.option("--out", "out_dir", type=click.Path(path_type=Path), required=True)
@click.option("--day", type=_DAY, default="2026-05-07", show_default=True)
@click.option("--seed", type=int, default=0, show_default=True)
@click.option("--with-unmodelled-features", is_flag=True, help="Add flexible-group / linked-window / joinedFamily markers.")
def synth(out_dir: Path, day, seed: int, with_unmodelled_features: bool) -> None:
    """Write a SYNTHETIC day (all 12 products) in the NESO layout."""
    from wattstack_ingestion.eac_history_synth import write_synth

    counts = write_synth(out_dir, day.date() if hasattr(day, "date") else day, seed,
                         with_unmodelled_features=with_unmodelled_features)
    click.echo(f"Wrote SYNTHETIC data to {out_dir}: {counts}")


if __name__ == "__main__":
    sys.exit(main())
