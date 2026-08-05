import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    import calendar
    from datetime import date, timedelta

    import marimo as mo
    import pandas as pd

    from wattstack_ingestion.analysis import (
        aggregate_volume_by_day_and_category,
        bid_volume,
        is_flagged,
        offer_volume,
    )
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.plots import stacked_bar_chart

    return (
        Cache,
        ElexonClient,
        aggregate_volume_by_day_and_category,
        bid_volume,
        date,
        is_flagged,
        mo,
        offer_volume,
        pd,
        stacked_bar_chart,
        timedelta,
    )


@app.cell
def _(mo):
    mo.md("""
    # SPAR: Daily volume of SO-Flagged / non-Flagged actions

    Reproduces "Daily volume of SO-Flagged/non-Flagged actions"
    from Elexon's System Prices Analysis Report.

    Source: https://www.elexon.co.uk/bsc/data/system-prices-analysis-report/

    **What SO-Flag actually means, quoted from Elexon's own
    methodology, not inferred:** the Imbalance Price calculation
    tries to separate "energy" balancing actions (which should set
    the price) from "system" balancing actions (locational
    constraint management, which shouldn't). "The System Operator
    (SO) flags actions when they are taken to resolve a locational
    constraint on the transmission network." Flagged actions
    aren't simply excluded -- they may be re-priced via a separate
    Classification/Replacement Price process. This is a distinct
    flag from CADL-Flag (short-duration corrective actions, <10
    min) -- this notebook targets SO-Flag specifically, matching
    the chart title.

    **"Buy" and "Sell" here are Elexon's own terms for the same
    direction split already built for the fuel-type notebook**:
    Buy actions are taken when the system is short (Offer-
    direction, `offer_volume()`); Sell actions when the system is
    long (Bid-direction, `bid_volume()`). Reused directly, not
    rebuilt.

    **Two real things stated plainly, not glossed over:** BOALF's
    full name is "Bid Offer Acceptance Level *Flagged*" -- strong
    evidence a flag field genuinely lives in this data, but the
    exact field name and value encoding (`true`/`false`? `"Y"`/
    `"N"`? `1`/`0`?) were not confirmed live -- `is_flagged()`
    handles the plausible encodings defensively. And direction
    (Buy/Sell) is still the same *inferred* signal as before (from
    level change), not a confirmed explicit field -- if the schema
    preview below reveals a real direction field, that would be
    worth switching to.
    """)
    return


@app.cell
def _(Cache, ElexonClient):
    cache = Cache("wattstack_ingestion_cache.sqlite")
    elexon = ElexonClient(cache=cache)
    return (elexon,)


@app.cell
def _(date, mo, timedelta):
    _default = (date.today().replace(day=1) - timedelta(days=32)).replace(day=1)
    month_picker = mo.ui.date(value=_default, label="Any date in the month to analyse")
    days_to_fetch = mo.ui.slider(1, 10, value=3, label="Days to fetch (start small!)")
    fetch = mo.ui.run_button(label="Fetch")
    mo.hstack([month_picker, days_to_fetch, fetch])
    return days_to_fetch, fetch, month_picker


@app.cell
def _(days_to_fetch, mo):
    mo.md(f"""
    *Acceptances are fetched one settlement period at a time -- {days_to_fetch.value} day(s) is ~{days_to_fetch.value * 48} requests.*
    """)
    return


@app.cell
def _(date, days_to_fetch, elexon, fetch, mo, month_picker, pd, timedelta):
    mo.stop(not fetch.value, mo.md("*Click Fetch to load acceptances.*"))

    _rows = []
    for _offset in range(days_to_fetch.value, 0, -1):
        _day = date(month_picker.value.year, month_picker.value.month, 1) + timedelta(days=_offset - 1)
        for _row in elexon.bid_offer_acceptances_for_day(_day):
            _row["_date"] = _day.isoformat()
            _rows.append(_row)
    acceptances_df = pd.DataFrame(_rows)

    mo.md(f"Fetched **{len(acceptances_df)}** acceptance rows. Real columns below.")
    return (acceptances_df,)


@app.cell
def _(acceptances_df, mo):
    mo.stop(acceptances_df.empty, mo.md("*No data yet.*"))
    mo.vstack([mo.md("**Real columns returned:**"), mo.ui.table(acceptances_df.head(5))])
    return


@app.cell
def _(acceptances_df, mo):
    mo.stop(acceptances_df.empty)
    level_from_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="Level-from field")
    level_to_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="Level-to field")
    so_flag_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="SO-Flag field")
    mo.hstack([level_from_field, level_to_field, so_flag_field])
    return level_from_field, level_to_field, so_flag_field


@app.cell
def _(mo):
    mo.md("""
    ## The table: every action, its direction, and its flag status

    Direction comes from `offer_volume()`/`bid_volume()` (level
    change); flag status from whichever field you picked above,
    run through `is_flagged()`. Rows with no net direction (equal
    level-from/level-to) are dropped -- they carry no volume
    either way. Check this looks sane before trusting the chart.
    """)
    return


@app.cell
def _(
    acceptances_df,
    bid_volume,
    is_flagged,
    level_from_field,
    level_to_field,
    mo,
    offer_volume,
    pd,
    so_flag_field,
):
    mo.stop(
        not all([level_from_field.value, level_to_field.value, so_flag_field.value]),
        mo.md("*Pick all three field mappings above.*"),
    )

    _categorised = []
    for _row in acceptances_df.to_dict("records"):
        _lf = _row.get(level_from_field.value, 0.0) or 0.0
        _lt = _row.get(level_to_field.value, 0.0) or 0.0
        _offer = offer_volume(_lf, _lt)
        _bid = bid_volume(_lf, _lt)
        _flagged = is_flagged(_row.get(so_flag_field.value))
        _flag_label = "SO-Flagged" if _flagged else "Unflagged"

        if _offer > 0:
            _categorised.append({"date": _row["_date"], "direction": "Buy", "flag": _flag_label, "volume": _offer})
        elif _bid > 0:
            _categorised.append({"date": _row["_date"], "direction": "Sell", "flag": _flag_label, "volume": _bid})

    categorised_df = pd.DataFrame(_categorised)
    if not categorised_df.empty:
        categorised_df["category"] = categorised_df["direction"] + " (" + categorised_df["flag"] + ")"
    mo.ui.table(categorised_df, page_size=15)
    return (categorised_df,)


@app.cell
def _(categorised_df, mo):
    mo.stop(categorised_df.empty, mo.md("*No categorised rows yet.*"))
    _summary = categorised_df.groupby(["direction", "flag"])["volume"].sum()
    _total_by_direction = categorised_df.groupby("direction")["volume"].sum()
    _lines = ["**Percent flagged, by direction** -- compare against a real published SPAR figure if you picked "
              "a month it covers (e.g. September 2025: 80% of Sell volume was SO-Flagged):"]
    for _direction in _total_by_direction.index:
        _flagged_vol = _summary.get((_direction, "SO-Flagged"), 0.0)
        _pct = _flagged_vol / _total_by_direction[_direction] * 100 if _total_by_direction[_direction] else 0.0
        _lines.append(f"- {_direction}: {_pct:.0f}% SO-Flagged")
    mo.md("\n".join(_lines))
    return


@app.cell
def _(mo):
    mo.md("""
    ## The chart: daily volume by direction and flag status
    """)
    return


@app.cell
def _(
    aggregate_volume_by_day_and_category,
    categorised_df,
    mo,
    stacked_bar_chart,
):
    mo.stop(categorised_df.empty)

    _agg = aggregate_volume_by_day_and_category(
        categorised_df.to_dict("records"), date_field="date", category_field="category", volume_field="volume"
    )
    stacked_bar_chart(
        categories=_agg["dates"],
        series={c: _agg["volumes"][c] for c in _agg["categories"]},
        title="Daily volume: SO-Flagged vs Unflagged, Buy vs Sell",
        x_label="Date",
        y_label="Volume (proxy, MW-level-change)",
    )
    return


if __name__ == "__main__":
    app.run()
