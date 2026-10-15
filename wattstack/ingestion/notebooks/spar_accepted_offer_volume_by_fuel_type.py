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
        filter_battery_bmu_ids,
        filter_bmus_by_id_pattern,
        fuel_type_lookup,
        offer_volume,
    )
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.plots import stacked_bar_chart
    return (
        Cache,
        ElexonClient,
        aggregate_volume_by_day_and_category,
        calendar,
        date,
        filter_battery_bmu_ids,
        filter_bmus_by_id_pattern,
        fuel_type_lookup,
        mo,
        offer_volume,
        pd,
        stacked_bar_chart,
        timedelta,
    )


@app.cell
def _(mo):
    mo.md(
        """
        # SPAR: Accepted Offer Volume by Fuel Type

        Reproduces "Accepted Offer Volume by Fuel Type" from Elexon's
        System Prices Analysis Report: daily accepted Offer volume
        (balancing actions taken to *increase* energy on the system),
        stacked by fuel type, **including BSAA** as its own segment.

        Source: https://www.elexon.co.uk/bsc/data/system-prices-analysis-report/

        **BSAA** (Balancing Services Adjustment Actions) are volume
        from *outside* the ordinary Bid/Offer stack -- system-to-system
        services, STOR taken outside the BM, forward contracted energy
        products. Fetched here from DISBSAD, a genuinely separate
        dataset from the accepted Bid/Offer actions (BOALF).

        **A real approximation, stated plainly rather than left
        implicit:** Elexon's own acceptance data doesn't carry an
        explicit "this was an Offer" flag -- direction is inferred
        here from whether a unit's instructed level *increased*
        within an acceptance (`offer_volume()`), and the resulting
        number is a relative-magnitude proxy, not a precise
        settlement-grade MWh figure (that needs the acceptance's exact
        delivery duration, which isn't used here). Good for comparing
        which fuel types contributed most; not a number to quote as
        exact.

        **BESS is a special case, confirmed against live data:**
        `fuelType` on the BM unit reference data does not reliably tag
        battery storage at all. Battery identification here is the
        union of a fuel-type label match and an optional BM-unit-ID
        regex, the same combined approach used in `explore.py` (see
        ADR 0007) -- and the same warning applies: an ID pattern that
        looks reasonable can still catch real non-battery units (e.g.
        Dungeness B), so the matched-units table below is there to be
        checked, not skipped.
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
    days_to_fetch = mo.ui.slider(1, 31, value=3, label="Days of the month to fetch (start small!)")
    fetch = mo.ui.run_button(label="Fetch")
    mo.hstack([month_picker, days_to_fetch, fetch])
    return days_to_fetch, fetch, month_picker


@app.cell
def _(calendar, days_to_fetch, mo, month_picker):
    _actual_days = min(days_to_fetch.value, calendar.monthrange(month_picker.value.year, month_picker.value.month)[1])
    mo.md(
        f"*This fetches acceptances AND DISBSAD, one settlement period at a time, for "
        f"**{_actual_days} day(s)** -- approximately **{_actual_days * 96} requests** to a free, "
        f"no-API-key public service. A full month is ~2,900 requests total; raise the slider "
        f"once a small run looks right, not before.*"
    )
    return


@app.cell
def _(calendar, date, days_to_fetch, elexon, fetch, mo, month_picker, pd):
    mo.stop(not fetch.value, mo.md("*Pick a month and how many days, then click Fetch.*"))

    _year, _month = month_picker.value.year, month_picker.value.month
    _days_in_month = calendar.monthrange(_year, _month)[1]
    _n_days = min(days_to_fetch.value, _days_in_month)

    _acceptance_rows = []
    _bsad_rows = []
    for _day_num in range(1, _n_days + 1):
        _day = date(_year, _month, _day_num)
        for _row in elexon.bid_offer_acceptances_for_day(_day):
            _row["_date"] = _day.isoformat()
            _acceptance_rows.append(_row)
        for _row in elexon.disaggregated_bsad_for_day(_day):
            _row["_date"] = _day.isoformat()
            _bsad_rows.append(_row)

    acceptances_df = pd.DataFrame(_acceptance_rows)
    bsad_df = pd.DataFrame(_bsad_rows)
    bmunits_df = pd.DataFrame(elexon.bm_units_reference())

    mo.md(
        f"Fetched **{len(acceptances_df)}** acceptance rows, **{len(bsad_df)}** DISBSAD rows, and "
        f"**{len(bmunits_df)}** BM unit reference rows, for {_n_days} day(s) of {month_picker.value.strftime('%B %Y')}."
    )
    return acceptances_df, bmunits_df, bsad_df


@app.cell
def _(acceptances_df, bmunits_df, bsad_df, mo):
    mo.stop(acceptances_df.empty, mo.md("*No data yet.*"))
    mo.vstack(
        [
            mo.md("**Acceptances (BOALF) columns:**"),
            mo.ui.table(acceptances_df.head(5)),
            mo.md("**DISBSAD (BSAA) columns:**"),
            mo.ui.table(bsad_df.head(5)) if not bsad_df.empty else mo.md("*No DISBSAD rows in this window.*"),
            mo.md("**BM unit reference columns:**"),
            mo.ui.table(bmunits_df.head(5)),
        ]
    )
    return


@app.cell
def _(acceptances_df, bmunits_df, bsad_df, mo):
    mo.stop(acceptances_df.empty)
    bmu_id_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="BM unit ID field (acceptances)")
    level_from_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="Level-from field")
    level_to_field = mo.ui.dropdown(options=list(acceptances_df.columns), label="Level-to field")
    bsad_volume_field = mo.ui.dropdown(
        options=list(bsad_df.columns) if not bsad_df.empty else [], label="DISBSAD volume field"
    )
    ref_id_field = mo.ui.dropdown(options=list(bmunits_df.columns), label="BM unit ID field (reference)")
    fuel_field = mo.ui.dropdown(options=list(bmunits_df.columns), label="Fuel/technology field")
    battery_labels = mo.ui.text(value="battery", label="Battery label match (comma-separated)")
    id_pattern = mo.ui.text(
        value="",
        label="Battery ID pattern (regex, optional)",
        placeholder=r"e.g. B-\d+$ -- CHECK THE MATCHES BELOW, this risks false positives",
    )
    mo.vstack(
        [
            mo.hstack([bmu_id_field, level_from_field, level_to_field]),
            mo.hstack([bsad_volume_field, ref_id_field, fuel_field]),
            mo.hstack([battery_labels, id_pattern]),
        ]
    )
    return (
        battery_labels,
        bmu_id_field,
        bsad_volume_field,
        fuel_field,
        id_pattern,
        level_from_field,
        level_to_field,
        ref_id_field,
    )


@app.cell
def _(
    battery_labels,
    bmunits_df,
    filter_battery_bmu_ids,
    filter_bmus_by_id_pattern,
    fuel_field,
    id_pattern,
    mo,
    pd,
    ref_id_field,
):
    mo.stop(not (ref_id_field.value and fuel_field.value), mo.md("*Pick the BM unit reference field mappings above.*"))

    _labels = {label.strip() for label in battery_labels.value.split(",") if label.strip()}
    _by_fuel_type = filter_battery_bmu_ids(
        bmunits_df.to_dict("records"), id_field=ref_id_field.value, label_field=fuel_field.value,
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
                f"overlap counted once). These get their own **BESS** category below, overriding "
                f"whatever `fuelType` says for them. **Check this list** -- an ID-pattern match in "
                f"particular can include real non-battery units."
            ),
            mo.ui.table(
                bmunits_df[bmunits_df[ref_id_field.value].isin(battery_ids)]
                if battery_ids else pd.DataFrame({"note": ["No matches -- try a fuel-type label or ID pattern"]})
            ),
        ]
    )
    return (battery_ids,)


@app.cell
def _(mo):
    mo.md(
        """
        ## The table: every action, its inferred category, and its volume

        Each accepted row below is categorised by fuel type (via the
        BM unit reference lookup), except units matched as batteries
        above -- those are forced to category `"BESS"` regardless of
        what `fuelType` says, since it doesn't reliably say anything
        useful for them. Reduced to an `offer_volume` -- zero for any
        row whose level *decreased* (Bid-direction, not counted here).
        BSAA rows all fall under the fixed category `"BSAA"`. This
        table is what actually feeds the stacked chart -- worth
        checking it looks sane before trusting the chart.
        """
    )
    return


@app.cell
def _(
    acceptances_df,
    battery_ids,
    bmu_id_field,
    bmunits_df,
    bsad_df,
    bsad_volume_field,
    fuel_field,
    fuel_type_lookup,
    level_from_field,
    level_to_field,
    mo,
    offer_volume,
    pd,
    ref_id_field,
):
    mo.stop(
        not all([bmu_id_field.value, level_from_field.value, level_to_field.value, ref_id_field.value, fuel_field.value]),
        mo.md("*Pick the field mappings above (DISBSAD volume field only needed if BSAA rows exist).*"),
    )

    _fuel_lookup = fuel_type_lookup(bmunits_df.to_dict("records"), id_field=ref_id_field.value, fuel_type_field=fuel_field.value)

    _categorised = []
    for _row in acceptances_df.to_dict("records"):
        _bmu = _row.get(bmu_id_field.value)
        _vol = offer_volume(_row.get(level_from_field.value, 0.0) or 0.0, _row.get(level_to_field.value, 0.0) or 0.0)
        _category = "BESS" if _bmu in battery_ids else _fuel_lookup.get(_bmu, "Unknown")
        _categorised.append({"date": _row["_date"], "bmu_or_source": _bmu, "category": _category, "volume": _vol})

    if not bsad_df.empty and bsad_volume_field.value:
        for _row in bsad_df.to_dict("records"):
            _categorised.append({
                "date": _row["_date"], "bmu_or_source": "BSAA",
                "category": "BSAA", "volume": _row.get(bsad_volume_field.value, 0.0) or 0.0,
            })

    categorised_df = pd.DataFrame(_categorised)
    categorised_df = categorised_df[categorised_df["volume"] > 0]  # zero-volume (bid-direction) rows add nothing to this chart
    mo.ui.table(categorised_df, page_size=15)
    return (categorised_df,)


@app.cell
def _(mo):
    mo.md("## The chart: accepted Offer volume by day, stacked by fuel type")
    return


@app.cell
def _(aggregate_volume_by_day_and_category, categorised_df, mo, stacked_bar_chart):
    mo.stop(categorised_df.empty, mo.md("*Nothing to chart -- no positive-volume rows survived categorisation.*"))

    _agg = aggregate_volume_by_day_and_category(
        categorised_df.to_dict("records"), date_field="date", category_field="category", volume_field="volume"
    )
    stacked_bar_chart(
        categories=_agg["dates"],
        series={c: _agg["volumes"][c] for c in _agg["categories"]},
        title="Accepted Offer volume by fuel type (incl. BSAA)",
        x_label="Date",
        y_label="Offer volume (proxy, MW-level-change)",
    )
    return


if __name__ == "__main__":
    app.run()
