import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    import calendar
    from datetime import date, timedelta

    import marimo as mo
    import pandas as pd

    from wattstack_ingestion.analysis import bin_counts_by_group, classify_system_length
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.plots import grouped_bar_chart
    return (
        Cache,
        ElexonClient,
        bin_counts_by_group,
        calendar,
        classify_system_length,
        date,
        grouped_bar_chart,
        mo,
        pd,
        timedelta,
    )


@app.cell
def _(mo):
    mo.md(
        """
        # SPAR: Frequency of System Prices per month

        Reproduces one chart from Elexon's System Prices Analysis Report
        (SPAR): the count of settlement periods per month, binned in
        GBP20/MWh system-price buckets, split by whether the system was
        **long** or **short** that period.

        Source: https://www.elexon.co.uk/bsc/data/system-prices-analysis-report/
        -- note the SPAR report itself stopped being published after
        September 2025, but the underlying System Price data it was
        built from is still live via the Insights API, which is what
        this notebook uses directly.

        **Long vs short, from Elexon's own methodology page (not
        inferred):** short NIVs (Net Imbalance Volume) are positive,
        long NIVs are negative. When the system is short, System
        Prices are driven predominantly by the System Operator's buy
        actions (Offers); when long, predominantly by sell actions
        (Bids). See `analysis.classify_system_length()`.
        """
    )
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
    fetch = mo.ui.run_button(label="Fetch month")
    mo.vstack(
        [
            mo.md("*One request per day in the month (~28-31 calls) -- system prices are a per-day fetch, not per-period.*"),
            mo.hstack([month_picker, fetch]),
        ]
    )
    return fetch, month_picker


@app.cell
def _(calendar, date, elexon, fetch, mo, month_picker, pd):
    mo.stop(not fetch.value, mo.md("*Pick a month and click Fetch.*"))

    _year, _month = month_picker.value.year, month_picker.value.month
    _n_days = calendar.monthrange(_year, _month)[1]

    _rows = []
    for _day_num in range(1, _n_days + 1):
        _rows.extend(elexon.system_prices(date(_year, _month, _day_num)))
    raw_df = pd.DataFrame(_rows)

    mo.md(f"Fetched **{len(raw_df)}** settlement-period rows for {month_picker.value.strftime('%B %Y')}. Real columns below.")
    return (raw_df,)


@app.cell
def _(mo, raw_df):
    mo.stop(raw_df.empty, mo.md("*No data yet.*"))
    mo.vstack([mo.md("**Real columns returned:**"), mo.ui.table(raw_df.head(5))])
    return


@app.cell
def _(mo, raw_df):
    mo.stop(raw_df.empty)
    price_field = mo.ui.dropdown(
        options=list(raw_df.columns),
        value="systemSellPrice" if "systemSellPrice" in raw_df.columns else None,
        label="System price field",
    )
    niv_field = mo.ui.dropdown(options=list(raw_df.columns), label="Net Imbalance Volume field")
    mo.hstack([price_field, niv_field])
    return niv_field, price_field


@app.cell
def _(mo):
    mo.md(
        """
        ## The table: what actually makes a period long or short

        Every settlement period below carries its own NIV -- the sign
        of that single number, not the price itself, is what
        `system_length` is derived from (`classify_system_length()`:
        NIV >= 0 -> Short, NIV < 0 -> Long). Nothing about price level
        feeds into this classification; a long period and a short
        period can, and do, have overlapping prices -- that overlap is
        the whole reason the frequency chart below is worth looking at
        split by length rather than as one combined distribution.
        """
    )
    return


@app.cell
def _(classify_system_length, mo, niv_field, price_field, raw_df):
    mo.stop(
        not (price_field.value and niv_field.value),
        mo.md("*Pick both field mappings above.*"),
    )

    clean_df = raw_df[["settlementDate", "settlementPeriod", price_field.value, niv_field.value]].copy()
    clean_df.columns = ["settlement_date", "settlement_period", "system_price", "niv_mwh"]
    clean_df["system_length"] = clean_df["niv_mwh"].apply(classify_system_length)

    mo.ui.table(clean_df, page_size=15)
    return (clean_df,)


@app.cell
def _(clean_df, mo):
    mo.stop(clean_df.empty)
    _summary = clean_df.groupby("system_length")["system_price"].agg(["count", "min", "max", "median", "mean", "std"]).round(2)
    mo.vstack(
        [
            mo.md(
                "**Summary by length** -- if you picked September 2025, compare directly against Elexon's own "
                "published table: Long mean GBP36.30/MWh (median 52.25), Short mean GBP102.70/MWh (median 104.50)."
            ),
            mo.ui.table(_summary.reset_index()),
        ]
    )
    return


@app.cell
def _(mo):
    mo.md("## The chart: frequency of System Prices, binned GBP20/MWh, by length")
    return


@app.cell
def _(bin_counts_by_group, clean_df, grouped_bar_chart, mo):
    mo.stop(clean_df.empty)

    _binned = bin_counts_by_group(
        clean_df["system_price"].tolist(), clean_df["system_length"].tolist(), bin_width=20.0
    )
    grouped_bar_chart(
        categories=_binned["bin_labels"],
        series={g: _binned["counts"][g] for g in _binned["groups"]},
        title="Frequency of System Prices, by length (GBP20/MWh bins)",
        x_label="System Price bin (GBP/MWh)",
        y_label="Settlement periods",
    )
    return


if __name__ == "__main__":
    app.run()
