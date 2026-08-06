import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    from datetime import date, datetime, timedelta, timezone

    import marimo as mo
    import pandas as pd

    from wattstack_ingestion.analysis import bin_counts_by_group, classify_system_length
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.forecasts import ElexonDemandForecastProvider
    from wattstack_ingestion.plots import grouped_bar_chart

    return (
        Cache,
        ElexonClient,
        ElexonDemandForecastProvider,
        bin_counts_by_group,
        classify_system_length,
        date,
        datetime,
        grouped_bar_chart,
        mo,
        pd,
        timedelta,
        timezone,
    )


@app.cell
def _(mo):
    mo.md("""
    # Demand forecast vs. system tightness

    Phase B's first real *consumer* of `ForecastProvider` (ADR
    0009), not just a proof the vintage-retrieval mechanism works.
    Demand forecast isn't a price the optimizer can use directly
    -- this asks a narrower, checkable question first: does a day
    ahead's forecast demand level actually predict whether the
    system turns out Long or Short?

    This is deliberately the simple predecessor to the
    LOLP-calibrated BM proxy from the roadmap, not the real thing
    -- LOLP/margin forecasts aren't wired up yet (next step, by
    design). Demand alone is a real, if cruder, tightness signal:
    high forecast demand plausibly correlates with the system
    being short more often. Worth finding out before assuming it.

    **Vintage matters here, not just data.** Each day's forecast
    is fetched via `as_of()` at **10:00 UTC the day before** --
    matching the day-ahead trigger window from the roadmap (just
    after N2EX gate closure, confirmed 09:50 GMT). This is what
    "what would actually have been known at decision time" means
    in practice, not a synonym for "the latest forecast available
    now."

    **Real simplification, stated plainly:** demand forecast rows
    aren't filtered by settlement date -- Elexon's field names for
    this endpoint aren't confirmed (see ADR 0009), so every row
    returned by one `as_of()` call is treated as belonging to the
    day it was fetched for. If the real response spans more than
    one day, this will need revisiting once you can see it live.
    """)
    return


@app.cell
def _(Cache, ElexonClient, ElexonDemandForecastProvider):
    cache = Cache("wattstack_ingestion_cache.sqlite")
    elexon = ElexonClient(cache=cache)
    forecast_provider = ElexonDemandForecastProvider(client=elexon)
    return elexon, forecast_provider


@app.cell
def _(date, mo, timedelta):
    _default = date.today() - timedelta(days=10)
    start_date = mo.ui.date(value=_default, label="First day to analyse")
    days_to_fetch = mo.ui.slider(1, 21, value=7, label="Days to fetch")
    fetch = mo.ui.run_button(label="Fetch")
    mo.vstack(
        [
            mo.md("*Two calls per day (one forecast, one actual) -- cheap compared to the acceptances notebooks.*"),
            mo.hstack([start_date, days_to_fetch, fetch]),
        ]
    )
    return days_to_fetch, fetch, start_date


@app.cell
def _(
    datetime,
    days_to_fetch,
    elexon,
    fetch,
    forecast_provider,
    mo,
    pd,
    start_date,
    timedelta,
    timezone,
):
    mo.stop(not fetch.value, mo.md("*Pick a start date and click Fetch.*"))

    _forecast_rows = []
    _actual_rows = []
    for _offset in range(days_to_fetch.value):
        _day = start_date.value + timedelta(days=_offset)
        _trigger_time = datetime(_day.year, _day.month, _day.day, tzinfo=timezone.utc) - timedelta(hours=14)
        # 10:00 UTC the day before _day, i.e. the day-ahead trigger window

        for _row in forecast_provider.as_of(_trigger_time):
            _row["_day"] = _day.isoformat()
            _forecast_rows.append(_row)
        for _row in elexon.system_prices(_day):
            _row["_day"] = _day.isoformat()
            _actual_rows.append(_row)

    forecast_df = pd.DataFrame(_forecast_rows)
    actual_df = pd.DataFrame(_actual_rows)

    mo.md(
        f"Fetched **{len(forecast_df)}** forecast rows and **{len(actual_df)}** actual settlement-period rows "
        f"across {days_to_fetch.value} day(s). Real columns below."
    )
    return actual_df, forecast_df


@app.cell
def _(actual_df, forecast_df, mo):
    mo.stop(forecast_df.empty, mo.md("*No data yet.*"))
    mo.vstack(
        [
            mo.md("**Forecast columns (unconfirmed field names -- this is where you'll see the real ones):**"),
            mo.ui.table(forecast_df.head(5)),
            mo.md("**Actual settlement-period columns (confirmed: settlementDate, settlementPeriod, systemSellPrice, systemBuyPrice; NIV field unconfirmed):**"),
            mo.ui.table(actual_df.head(5)),
        ]
    )
    return


@app.cell
def _(actual_df, forecast_df, mo):
    mo.stop(forecast_df.empty)
    forecast_period_field = mo.ui.dropdown(options=list(forecast_df.columns), label="Settlement period field (forecast)")
    demand_field = mo.ui.dropdown(options=list(forecast_df.columns), label="Demand value field (forecast)")
    price_field = mo.ui.dropdown(options=list(actual_df.columns), label="Price field (actual)")
    niv_field = mo.ui.dropdown(options=list(actual_df.columns), label="NIV field (actual)")
    bin_width = mo.ui.number(value=1000, label="Demand bin width (same units as the real field -- adjust once you see it)")
    mo.vstack(
        [
            mo.hstack([forecast_period_field, demand_field]),
            mo.hstack([price_field, niv_field]),
            bin_width,
        ]
    )
    return (
        bin_width,
        demand_field,
        forecast_period_field,
        niv_field,
        price_field,
    )


@app.cell
def _(mo):
    mo.md("""
    ## The table: forecast demand joined against what actually happened

    Joined on `(_day, settlement_period)` -- the day I already know
    from the fetch loop, not a parsed date field from either raw
    response. `system_length` comes straight from
    `classify_system_length()`, already validated against Elexon's
    own published SPAR figures in an earlier notebook, not new
    logic written for this one.
    """)
    return


@app.cell
def _(
    actual_df,
    classify_system_length,
    demand_field,
    forecast_df,
    forecast_period_field,
    mo,
    niv_field,
    pd,
    price_field,
):
    mo.stop(
        not all([forecast_period_field.value, demand_field.value, price_field.value, niv_field.value]),
        mo.md("*Pick all four field mappings above.*"),
    )

    _actual_by_key = {
        (row["_day"], row.get("settlementPeriod")): row for row in actual_df.to_dict("records")
    }

    _joined = []
    for _row in forecast_df.to_dict("records"):
        _key = (_row["_day"], _row.get(forecast_period_field.value))
        _actual = _actual_by_key.get(_key)
        if _actual is None:
            continue
        _joined.append({
            "day": _row["_day"],
            "settlement_period": _row.get(forecast_period_field.value),
            "forecast_demand": _row.get(demand_field.value),
            "actual_price": _actual.get(price_field.value),
            "system_length": classify_system_length(_actual.get(niv_field.value, 0.0) or 0.0),
        })

    joined_df = pd.DataFrame(_joined)
    mo.ui.table(joined_df, page_size=15)
    return (joined_df,)


@app.cell
def _(mo):
    mo.md("""
    ## The chart: does forecast demand predict Long vs Short?
    """)
    return


@app.cell
def _(bin_counts_by_group, bin_width, grouped_bar_chart, joined_df, mo):
    mo.stop(joined_df.empty, mo.md("*No joined rows -- check the settlement-period values actually line up between forecast and actual.*"))

    _binned = bin_counts_by_group(
        joined_df["forecast_demand"].tolist(), joined_df["system_length"].tolist(), bin_width=float(bin_width.value)
    )
    grouped_bar_chart(
        categories=_binned["bin_labels"],
        series={g: _binned["counts"][g] for g in _binned["groups"]},
        title="Settlement periods by forecast demand bin, Long vs Short",
        x_label="Forecast demand bin",
        y_label="Settlement periods",
    )
    return


if __name__ == "__main__":
    app.run()
