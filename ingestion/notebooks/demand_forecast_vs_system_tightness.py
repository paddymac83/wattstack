import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    from datetime import date, datetime, timedelta, timezone

    import marimo as mo
    import pandas as pd

    from wattstack_ingestion.analysis import bin_counts_by_group, classify_system_length, spread_by_bin
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.forecasts import ElexonDemandForecastProvider, ElexonWindForecastProvider
    from wattstack_ingestion.plots import grouped_bar_chart, spread_chart

    return (
        Cache,
        ElexonClient,
        ElexonDemandForecastProvider,
        ElexonWindForecastProvider,
        bin_counts_by_group,
        classify_system_length,
        date,
        datetime,
        grouped_bar_chart,
        mo,
        pd,
        spread_by_bin,
        spread_chart,
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

    ## Section 2 -- LOLP/margin

    A genuinely different shape from demand forecast, not just a
    second variable: LOLPDRM (Loss of Load Probability and
    De-rated Margin) has no `publishTime`/history mechanism at
    all. One call instead returns **five forecast horizons at
    once** (1h, 2h, 4h, 8h, "12h+" ahead of each period), confirmed
    directly from Elexon's own API client source. Given the
    day-ahead trigger convention above, every period of the target
    day is at least 14 hours ahead of the 10:00 UTC trigger -- so
    the "12h+" column is the semantically right one for every
    period, not a compromise. That's a real, checkable prediction;
    this is where you check it.

    **Deliberately not wrapped in `ForecastProvider` yet.** Forcing
    a five-horizons-per-call dataset into `as_of()`'s
    one-publish-time shape would mean guessing a column name I
    can't confirm. See `docs/adr/0010` for why this stayed a plain
    client method instead.

    **Result (`docs/adr/0012`): LOLP/margin was tested against
    real winter and summer weeks and rejected as a tightness
    signal.** LOLP sits at ~0 across every horizon (correct
    behaviour -- it measures rare capacity-adequacy risk, not
    routine balancing noise) and margin shows no relationship to
    NIV direction. Wind, below, is the next candidate -- not
    because it's assumed better, but because wind forecast error
    is the actual dominant driver of the short-term imbalance that
    margin turned out not to explain.

    ## Section 3 -- wind

    A different question from Sections 1 and 2, on purpose: this
    asks whether wind forecast predicts **volatility** (how spread
    out prices get), not direction (which way they lean). Reusing
    the direction-counting approach from demand/LOLP would answer
    the wrong question -- `spread_by_bin()` instead computes the
    standard deviation of actual price within each wind-forecast
    bucket, the more literal answer to "does this predict how
    volatile the day will be."

    Wind genuinely fits `ForecastProvider` -- confirmed directly
    from Elexon's own API documentation, a real `history` endpoint
    exists, unlike LOLP. Published up to 8 times a day at fixed
    times (03:30, 05:30, 08:30, 10:30, 12:30, 16:30, 19:30,
    23:30); the day-ahead trigger at 10:00 UTC falls just after
    the 08:30 publication, so the `as_of()` call below resolves to
    that vintage, not a synthetic "10:00 exactly" forecast that
    never existed.
    """)
    return


@app.cell
def _(
    Cache,
    ElexonClient,
    ElexonDemandForecastProvider,
    ElexonWindForecastProvider,
):
    cache = Cache("wattstack_ingestion_cache.sqlite")
    elexon = ElexonClient(cache=cache)
    forecast_provider = ElexonDemandForecastProvider(client=elexon)
    wind_provider = ElexonWindForecastProvider(client=elexon)
    return elexon, forecast_provider, wind_provider


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


@app.cell
def _(
    datetime,
    days_to_fetch,
    elexon,
    fetch,
    mo,
    pd,
    start_date,
    timedelta,
    timezone,
):
    mo.stop(not fetch.value, mo.md("*Click Fetch above to also load LOLP/margin data.*"))

    _from = datetime(start_date.value.year, start_date.value.month, start_date.value.day, tzinfo=timezone.utc)
    _to = _from + timedelta(days=days_to_fetch.value)
    lolp_rows = elexon.loss_of_load_forecast(_from, _to)
    lolp_df = pd.DataFrame(lolp_rows)

    mo.md(f"Fetched **{len(lolp_df)}** LOLP/margin rows in a single call -- no per-day loop needed for this endpoint.")
    return (lolp_df,)


@app.cell
def _(lolp_df, mo):
    mo.stop(lolp_df.empty, mo.md("*No LOLP/margin data yet.*"))
    mo.vstack([mo.md("**LOLP/margin columns (unconfirmed -- five horizons live in here somewhere):**"), mo.ui.table(lolp_df.head(5))])
    return


@app.cell
def _(lolp_df, mo):
    mo.stop(lolp_df.empty)
    lolp_date_field = mo.ui.dropdown(options=list(lolp_df.columns), label="Settlement date field (LOLP)")
    lolp_period_field = mo.ui.dropdown(options=list(lolp_df.columns), label="Settlement period field (LOLP)")
    lolp_value_field = mo.ui.dropdown(options=list(lolp_df.columns), label="Value field to explore (pick a horizon column)")
    lolp_bin_width = mo.ui.number(value=1, label="LOLP/margin bin width (unknown scale -- adjust once you see it)")
    mo.vstack([mo.hstack([lolp_date_field, lolp_period_field]), mo.hstack([lolp_value_field, lolp_bin_width])])
    return lolp_bin_width, lolp_date_field, lolp_period_field, lolp_value_field


@app.cell
def _(mo):
    mo.md("""
    ### The table: LOLP/margin added to the existing demand comparison

    Joined onto `joined_df` above by `(day, settlement_period)` --
    LOLP's own date field is truncated to its first 10 characters
    to tolerate either a bare date or a full datetime string,
    since the real format isn't confirmed. Rows with no LOLP match
    are dropped here, not silently zero-filled.
    """)
    return


@app.cell
def _(
    joined_df,
    lolp_date_field,
    lolp_df,
    lolp_period_field,
    lolp_value_field,
    mo,
):
    mo.stop(
        not all([lolp_date_field.value, lolp_period_field.value, lolp_value_field.value]),
        mo.md("*Pick all three LOLP field mappings above.*"),
    )

    _lolp_by_key = {}
    for _row in lolp_df.to_dict("records"):
        _key = (str(_row.get(lolp_date_field.value, ""))[:10], _row.get(lolp_period_field.value))
        _lolp_by_key[_key] = _row.get(lolp_value_field.value)

    _with_lolp = joined_df.copy()
    _with_lolp["lolp_value"] = _with_lolp.apply(lambda r: _lolp_by_key.get((r["day"], r["settlement_period"])), axis=1)
    joined_with_lolp_df = _with_lolp.dropna(subset=["lolp_value"])

    mo.vstack([
        mo.md(f"**{len(joined_with_lolp_df)}** of {len(joined_df)} rows matched a LOLP value."),
        mo.ui.table(joined_with_lolp_df, page_size=15),
    ])
    return (joined_with_lolp_df,)


@app.cell
def _(mo):
    mo.md("""
    ### The chart: does this LOLP/margin horizon predict Long vs Short better than demand did?
    """)
    return


@app.cell
def _(
    bin_counts_by_group,
    grouped_bar_chart,
    joined_with_lolp_df,
    lolp_bin_width,
    mo,
):
    mo.stop(joined_with_lolp_df.empty, mo.md("*No matched rows to chart yet.*"))

    _binned = bin_counts_by_group(
        joined_with_lolp_df["lolp_value"].tolist(),
        joined_with_lolp_df["system_length"].tolist(),
        bin_width=float(lolp_bin_width.value),
    )
    grouped_bar_chart(
        categories=_binned["bin_labels"],
        series={g: _binned["counts"][g] for g in _binned["groups"]},
        title="Settlement periods by LOLP/margin bin, Long vs Short",
        x_label="LOLP/margin bin (scale unconfirmed)",
        y_label="Settlement periods",
    )
    return


@app.cell
def _(
    datetime,
    days_to_fetch,
    fetch,
    mo,
    pd,
    start_date,
    timedelta,
    timezone,
    wind_provider,
):
    mo.stop(not fetch.value, mo.md("*Click Fetch above to also load wind forecast data.*"))

    _wind_rows = []
    for _offset in range(days_to_fetch.value):
        _day = start_date.value + timedelta(days=_offset)
        _trigger_time = datetime(_day.year, _day.month, _day.day, tzinfo=timezone.utc) - timedelta(hours=14)
        # 10:00 UTC the day before _day -- same day-ahead trigger convention as Section 1;
        # the history endpoint resolves this to whichever real publication (likely 08:30) was current then.
        for _row in wind_provider.as_of(_trigger_time):
            _row["_day"] = _day.isoformat()
            _wind_rows.append(_row)

    wind_df = pd.DataFrame(_wind_rows)
    mo.md(f"Fetched **{len(wind_df)}** wind forecast rows across {days_to_fetch.value} day(s).")
    return (wind_df,)


@app.cell
def _(mo, wind_df):
    mo.stop(wind_df.empty, mo.md("*No wind forecast data yet.*"))
    mo.vstack([mo.md("**Wind forecast columns (unconfirmed field names):**"), mo.ui.table(wind_df.head(5))])
    return


@app.cell
def _(mo, wind_df):
    mo.stop(wind_df.empty)
    wind_period_field = mo.ui.dropdown(options=list(wind_df.columns), label="Settlement period field (wind)")
    wind_value_field = mo.ui.dropdown(options=list(wind_df.columns), label="Forecast generation value field (wind)")
    wind_bin_width = mo.ui.number(value=1000, label="Wind bin width (MW, presumed -- adjust once you see it)")
    mo.hstack([wind_period_field, wind_value_field, wind_bin_width])
    return wind_bin_width, wind_period_field, wind_value_field


@app.cell
def _(mo):
    mo.md("""
    ### The table: wind forecast added to the existing comparison

    Same join shape as LOLP -- `(day, settlement_period)`, wind's
    own row already tagged with the fetch-loop day, not a parsed
    date field. Unlike LOLP, this reuses the *joined_df* from
    Section 1 directly (demand's classification is unaffected by
    which of these two later sections you run).
    """)
    return


@app.cell
def _(joined_df, mo, wind_df, wind_period_field, wind_value_field):
    mo.stop(
        not all([wind_period_field.value, wind_value_field.value]),
        mo.md("*Pick both wind field mappings above.*"),
    )

    _wind_by_key = {
        (row["_day"], row.get(wind_period_field.value)): row.get(wind_value_field.value)
        for row in wind_df.to_dict("records")
    }

    _with_wind = joined_df.copy()
    _with_wind["wind_forecast"] = _with_wind.apply(
        lambda r: _wind_by_key.get((r["day"], r["settlement_period"])), axis=1
    )
    joined_with_wind_df = _with_wind.dropna(subset=["wind_forecast"])

    mo.vstack([
        mo.md(f"**{len(joined_with_wind_df)}** of {len(joined_df)} rows matched a wind forecast value."),
        mo.ui.table(joined_with_wind_df, page_size=15),
    ])
    return (joined_with_wind_df,)


@app.cell
def _(mo):
    mo.md("""
    ### The chart that actually answers "volatility": price spread by wind-forecast bucket

    `spread_by_bin()` -- mean and standard deviation of actual
    price within each wind-forecast bucket. A bucket where the
    error bar is visibly wider than the others is where wind
    forecast is predicting real volatility; a roughly flat set of
    error bars across buckets means wind forecast level, at least
    on its own, isn't the thing driving how much prices swing.
    Sample size is in the hover text -- a wide bar on 2
    observations is not the same finding as a wide bar on 40.
    """)
    return


@app.cell
def _(joined_with_wind_df, mo, spread_by_bin, spread_chart, wind_bin_width):
    mo.stop(joined_with_wind_df.empty, mo.md("*No matched rows to chart yet.*"))

    _spread = spread_by_bin(
        joined_with_wind_df["wind_forecast"].tolist(),
        joined_with_wind_df["actual_price"].tolist(),
        bin_width=float(wind_bin_width.value),
    )
    spread_chart(
        categories=_spread["bin_labels"],
        means=_spread["means"],
        std_devs=_spread["std_devs"],
        counts=_spread["counts"],
        title="Price spread by wind forecast bucket -- volatility, not direction",
        x_label="Wind forecast bucket (MW, presumed)",
        y_label="Actual price (mean +/- 1 std dev)",
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ### The secondary chart: does wind level predict direction too?

    Not what was asked, but cheap to check given the plumbing
    already exists -- reuses `bin_counts_by_group()`/
    `grouped_bar_chart()` exactly as Sections 1 and 2 did. Worth
    looking at alongside the spread chart above, not instead of
    it: a variable can predict volatility without predicting
    direction, and vice versa -- they're genuinely different
    questions, and it's worth seeing both answers rather than
    assuming one implies the other.
    """)
    return


@app.cell
def _(
    bin_counts_by_group,
    grouped_bar_chart,
    joined_with_wind_df,
    mo,
    wind_bin_width,
):
    mo.stop(joined_with_wind_df.empty)

    _binned = bin_counts_by_group(
        joined_with_wind_df["wind_forecast"].tolist(),
        joined_with_wind_df["system_length"].tolist(),
        bin_width=float(wind_bin_width.value),
    )
    grouped_bar_chart(
        categories=_binned["bin_labels"],
        series={g: _binned["counts"][g] for g in _binned["groups"]},
        title="Settlement periods by wind forecast bucket, Long vs Short",
        x_label="Wind forecast bucket (MW, presumed)",
        y_label="Settlement periods",
    )
    return


if __name__ == "__main__":
    app.run()
