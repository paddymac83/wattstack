import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import pandas as pd
    from datetime import date, timedelta

    from wattstack_ingestion.analysis import (
        filter_battery_bmu_ids,
        filter_bmus_by_id_pattern,
        marginal_bid_share,
        price_lookup_by_bmu_period,
    )
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.neso import NesoClient
    from wattstack_ingestion.plots import price_distribution
    return (
        Cache,
        ElexonClient,
        NesoClient,
        date,
        filter_battery_bmu_ids,
        filter_bmus_by_id_pattern,
        marginal_bid_share,
        mo,
        pd,
        price_distribution,
        price_lookup_by_bmu_period,
        timedelta,
    )


@app.cell
def _(mo):
    mo.md(
        """
        # wattstack data explorer

        Reactive: change a control, everything downstream recomputes
        automatically. This is a real `.py` file -- `git diff` it like
        any other code, `python explore.py` runs it headless.

        Nothing calls the network until you press a **Fetch** button --
        API calls are the slow, opt-in part, not something that should
        re-run on every keystroke.
        """
    )
    return


@app.cell
def _(Cache, ElexonClient, NesoClient):
    cache = Cache("wattstack_ingestion_cache.sqlite")
    elexon = ElexonClient(cache=cache)
    neso = NesoClient(cache=cache)
    return elexon, neso


@app.cell
def _(mo):
    mo.md("## Section 1 -- general explorer")
    return


@app.cell
def _(mo):
    source = mo.ui.dropdown(
        options=["Elexon system prices", "NESO response-reserve results"],
        value="Elexon system prices",
        label="Data source",
    )
    days_back = mo.ui.slider(1, 60, value=14, label="Days back")
    fetch_general = mo.ui.run_button(label="Fetch")
    mo.hstack([source, days_back, fetch_general])
    return days_back, fetch_general, source


@app.cell
def _(date, days_back, elexon, fetch_general, mo, neso, pd, source, timedelta):
    mo.stop(not fetch_general.value, mo.md("*Click Fetch to load data.*"))

    if source.value == "Elexon system prices":
        _rows = []
        for _offset in range(days_back.value, 0, -1):
            _day = date.today() - timedelta(days=_offset + 1)
            _rows.extend(elexon.system_prices(_day))
        general_df = pd.DataFrame(_rows)
    else:
        general_df = pd.DataFrame(neso.response_reserve_results_summary(limit=days_back.value * 50))

    shaped = mo.ui.dataframe(general_df)
    shaped
    return general_df, shaped


@app.cell
def _(mo, shaped):
    _df = shaped.value
    numeric_cols = [c for c in _df.columns if _df[c].dtype.kind in "if"]
    column = mo.ui.dropdown(
        options=numeric_cols, value=numeric_cols[0] if numeric_cols else None, label="Column to plot"
    )
    column
    return (column,)


@app.cell
def _(column, mo, price_distribution, shaped):
    mo.stop(column.value is None, mo.md("*No numeric columns to plot -- adjust the shaping above.*"))
    price_distribution(shaped.value[column.value].dropna().tolist(), f"{column.value} distribution", column.value)
    return


@app.cell
def _(mo):
    mo.md(
        """
        ## Section 2 -- worked example: was a battery the marginal bid?

        A deliberately simple starting definition, not BSC's actual
        price-setting methodology (which volume-weights across roughly
        the most extreme 1% of accepted volume, not a single action).

        This needs **two** datasets, not one -- a real correction, not
        a style choice: accepted actions (BOALF) record which BM unit
        was accepted and how much volume, but not price. Price comes
        from Bid-Offer Data (BOD), what each unit submitted. Getting a
        price for a specific acceptance means joining the two on BM
        unit and settlement period, and even that join is an
        approximation -- BOD is the full ladder a unit submitted, not
        a record of which rung a given acceptance used. See
        `analysis.py`'s docstring and ADR 0006 for the full story.
        Tune the field mappings below once you can see the real
        column names; treat the definition itself as editable.
        """
    )
    return


@app.cell
def _(mo):
    marginal_days_back = mo.ui.slider(1, 5, value=1, label="Days to fetch")
    fetch_marginal = mo.ui.run_button(label="Fetch")
    mo.vstack(
        [
            mo.md(
                "*Acceptances AND bid-offer data are each fetched one settlement "
                "period at a time -- a day now costs ~96 requests (48 + 48), not 48. "
                "Keep this small.*"
            ),
            mo.hstack([marginal_days_back, fetch_marginal]),
        ]
    )
    return fetch_marginal, marginal_days_back


@app.cell
def _(date, elexon, fetch_marginal, marginal_days_back, mo, pd, timedelta):
    mo.stop(not fetch_marginal.value, mo.md("*Click Fetch to load acceptances, bid-offer data, and BM unit reference data.*"))

    _acceptance_rows = []
    _bid_offer_rows = []
    for _offset in range(marginal_days_back.value, 0, -1):
        _day = date.today() - timedelta(days=_offset + 1)
        _acceptance_rows.extend(elexon.bid_offer_acceptances_for_day(_day))
        _bid_offer_rows.extend(elexon.bid_offer_data_for_day(_day))
    acceptances_df = pd.DataFrame(_acceptance_rows)
    bid_offer_df = pd.DataFrame(_bid_offer_rows)
    bmunits_df = pd.DataFrame(elexon.bm_units_reference())

    mo.md(
        f"Fetched {len(acceptances_df)} acceptance rows, {len(bid_offer_df)} bid-offer rows, "
        f"and {len(bmunits_df)} BM unit reference rows. Real column names below -- "
        "use them in the mappings underneath."
    )
    return acceptances_df, bid_offer_df, bmunits_df


@app.cell
def _(acceptances_df, bid_offer_df, bmunits_df, mo):
    mo.stop(acceptances_df.empty, mo.md("*No data yet.*"))
    mo.vstack(
        [
            mo.md("**Acceptances (BOALF) columns -- who/when/how much:**"),
            mo.ui.table(acceptances_df.head(5)),
            mo.md("**Bid-offer data (BOD) columns -- submitted prices:**"),
            mo.ui.table(bid_offer_df.head(5)),
            mo.md("**BM unit reference columns:**"),
            mo.ui.table(bmunits_df.head(5)),
        ]
    )
    return


@app.cell
def _(acceptances_df, bid_offer_df, bmunits_df, mo):
    mo.stop(acceptances_df.empty)
    period_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="Settlement period field (acceptances)")
    bmu_id_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="BM unit ID field (acceptances)")
    bo_period_field = mo.ui.dropdown(options=list(bid_offer_df.columns), label="Settlement period field (bid-offer)")
    bo_bmu_id_field = mo.ui.dropdown(options=list(bid_offer_df.columns), label="BM unit ID field (bid-offer)")
    price_field = mo.ui.dropdown(options=list(bid_offer_df.columns), label="Price field (bid-offer)")
    ref_id_field = mo.ui.dropdown(options=list(bmunits_df.columns), label="BM unit ID field (reference)")
    label_field = mo.ui.dropdown(options=list(bmunits_df.columns), label="Fuel/technology field")
    battery_labels = mo.ui.text(value="battery", label="Battery label match (comma-separated)")
    id_pattern = mo.ui.text(
        value="",
        label="Battery ID pattern (regex, optional)",
        placeholder=r"e.g. B-\d+$ -- CHECK THE MATCHES BELOW, this risks false positives",
    )
    mo.vstack(
        [
            mo.md("From acceptances (who/when was accepted):"),
            mo.hstack([period_field, bmu_id_field]),
            mo.md("From bid-offer data (what price was submitted):"),
            mo.hstack([bo_period_field, bo_bmu_id_field, price_field]),
            mo.md(
                "From BM unit reference (which units are batteries) -- fuelType often "
                "doesn't tag BESS at all (confirmed against live data), so an optional ID "
                "pattern is combined with it, not used alone:"
            ),
            mo.hstack([ref_id_field, label_field, battery_labels]),
            id_pattern,
        ]
    )
    return (
        battery_labels,
        bmu_id_field,
        bo_bmu_id_field,
        bo_period_field,
        id_pattern,
        label_field,
        period_field,
        price_field,
        ref_id_field,
    )


@app.cell
def _(
    bmunits_df,
    battery_labels,
    filter_battery_bmu_ids,
    filter_bmus_by_id_pattern,
    id_pattern,
    label_field,
    mo,
    pd,
    ref_id_field,
):
    mo.stop(not (ref_id_field.value and label_field.value), mo.md("*Pick the BM unit reference field mappings above.*"))

    _labels = {label.strip() for label in battery_labels.value.split(",") if label.strip()}
    _by_fuel_type = filter_battery_bmu_ids(
        bmunits_df.to_dict("records"), id_field=ref_id_field.value, label_field=label_field.value,
        battery_labels=_labels,
    )
    _by_id_pattern = (
        filter_bmus_by_id_pattern(bmunits_df.to_dict("records"), id_field=ref_id_field.value, pattern=id_pattern.value)
        if id_pattern.value
        else set()
    )
    battery_ids = _by_fuel_type | _by_id_pattern

    mo.vstack(
        [
            mo.md(
                f"**{len(battery_ids)} battery BM units matched** "
                f"({len(_by_fuel_type)} by fuel-type text, {len(_by_id_pattern)} by ID pattern -- "
                f"overlap counted once). **Check this list before trusting the chart below** -- "
                f"an ID-pattern match in particular can include real non-battery units."
            ),
            mo.ui.table(
                bmunits_df[bmunits_df[ref_id_field.value].isin(battery_ids)]
                if battery_ids else pd.DataFrame({"note": ["No matches -- try a fuel-type label or ID pattern"]})
            ),
        ]
    )
    return (battery_ids,)


@app.cell
def _(
    acceptances_df,
    battery_ids,
    bid_offer_df,
    bmu_id_field,
    bo_bmu_id_field,
    bo_period_field,
    marginal_bid_share,
    mo,
    period_field,
    price_field,
    price_lookup_by_bmu_period,
):
    mo.stop(
        not all([period_field.value, bmu_id_field.value, bo_period_field.value, bo_bmu_id_field.value, price_field.value]),
        mo.md("*Pick all field mappings above.*"),
    )

    price_lookup = price_lookup_by_bmu_period(
        bid_offer_df.to_dict("records"), bmu_id_field=bo_bmu_id_field.value,
        period_field=bo_period_field.value, price_field=price_field.value,
    )
    result = marginal_bid_share(
        acceptances_df.to_dict("records"), price_lookup, battery_ids,
        period_field=period_field.value, bmu_id_field=bmu_id_field.value,
    )

    mo.md(
        f"**{len(battery_ids)} battery BM units matched.** "
        f"**{len(price_lookup)}** (bm unit, period) prices known from bid-offer data. "
        f"Battery was the most extreme priced accepted action in **{result['share']:.1%}** "
        f"of {result['n_periods']} settlement periods with a known price "
        f"({len(acceptances_df[period_field.value].unique()) - result['n_periods']} periods excluded, no price match)."
    )
    return (result,)


if __name__ == "__main__":
    app.run()
