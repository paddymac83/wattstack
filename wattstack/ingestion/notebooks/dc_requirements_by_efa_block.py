import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    from datetime import datetime, timedelta, timezone

    import marimo as mo
    import pandas as pd

    from wattstack_ingestion.analysis import (
        EFA_BLOCKS,
        direction_from_sign,
        efa_block_label_for_index,
        filter_bmus_by_id_pattern,
        largest_value_by_group,
        spread_by_bin,
    )
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.neso import SYSTEM_INERTIA_RESOURCES, NesoClient
    from wattstack_ingestion.plots import spread_chart
    return (
        Cache,
        EFA_BLOCKS,
        ElexonClient,
        NesoClient,
        SYSTEM_INERTIA_RESOURCES,
        datetime,
        direction_from_sign,
        efa_block_label_for_index,
        filter_bmus_by_id_pattern,
        largest_value_by_group,
        mo,
        pd,
        spread_by_bin,
        spread_chart,
        timedelta,
        timezone,
    )


@app.cell
def _(mo):
    mo.md(
        """
        # Dynamic Containment: requirement volumes by EFA block

        Source: https://www.neso.energy/data-portal/dynamic-containment-4-day-forecast

        **Confirmed directly from NESO's own page, not inferred:**
        "The methodology uses forecasted demand, inertia, and response
        volumes as well as a view of the largest losses on the system
        to estimate the DC requirements." Four inputs, not three --
        demand is in there alongside inertia, largest losses, and
        other response volumes. Worth including if you extend this.

        Also confirmed directly: *"changes to interconnector flows
        from our forecasted position can lead to either an increase or
        decrease in our requirements if the change impacts the largest
        loss we need to secure"* -- the largest-loss driver is
        explicitly tied to interconnector import/export, exactly the
        "secured loss (import and export)" framing this notebook is
        built around.

        **EFA blocks**, confirmed via a real NESO Frequency Response
        Market Information Report: 6 blocks of 4 hours, starting
        23:00, 03:00, 07:00, 11:00, 15:00, 19:00. The EFA day itself
        starts at 23:00 the previous calendar day, not midnight.

        **Real gaps, stated plainly:**
        - Confirmed via live debugging (not by anything in this
          notebook's own tests until after the fact): the DC
          requirements CSV's date fields are day-first
          (`01/06/2026` = 1 June, not January 6). Parsed here with
          `dayfirst=True` -- without it, pandas silently misparses
          these as month-first, which is worse than a dropped row
          since it corrupts which calendar day (and therefore which
          EFA-block aggregate) the row belongs to, without raising
          any error.
        - This CSV is confirmed reachable but NOT confirmed
          datastore-active (no `datastore/dump/` link on NESO's page)
          -- fetched here via direct CSV parsing (`fetch_csv()`), a
          genuinely different access pattern from every other NESO
          method in this project.
        - No separate, confirmed dataset was found for "largest
          secured loss" or "other response product levels" as their
          own structured resources. The schema preview below is the
          first place to check whether either is exposed as a column
          in this CSV directly -- genuinely unknown until checked.
        - Section 2's inertia data is **outturn**, not a forward
          inertia forecast -- no separately confirmed "inertia
          forecast" dataset with a public endpoint was found. Testing
          the hypothesis with outturn-vs-outturn is honest; it isn't
          the same as matching forecast vintages the way demand/wind
          forecasts do elsewhere in this project.

        **Real schema, confirmed live -- corrects a wrong assumption
        this notebook started with.** The file is NOT one row per
        settlement period with separate DC-High/DC-Low columns. It's:
        `Forecast_Created`, `Forecast_Target_Date`, `Service_Type`,
        and six columns `EFA1`..`EFA6` holding the requirement (MW)
        for each EFA block directly -- no need to derive a block from
        an hour at all here, the block is which column the value came
        from. `Service_Type` splits DC-High from DC-Low across rows
        (long format), not columns. And there genuinely IS a vintage
        dimension in this file, which the earlier version of this
        notebook wrongly assumed didn't exist: `Forecast_Created` is
        when a given forecast row was generated, distinct from
        `Forecast_Target_Date`, the day it forecasts. That's not
        queryable via a publish-time parameter the way Elexon's
        `/history` endpoints are -- it's just a column in the data,
        filterable locally after fetching the whole file.

        **Why this connects to the acceptance-risk work already on the
        roadmap:** DC is pay-as-clear -- every accepted bid gets the
        same clearing price. That means the requirement *volume* is
        naturally a signal for *acceptance probability*, not price
        level: a higher requirement means more capacity clears, so a
        given bid is more likely to be among the accepted ones. Worth
        keeping in mind for that thread, not built here.
        """
    )
    return


@app.cell
def _(Cache, ElexonClient, NesoClient):
    cache = Cache("wattstack_ingestion_cache.sqlite")
    neso = NesoClient(cache=cache)
    elexon = ElexonClient(cache=cache)
    return elexon, neso


@app.cell
def _(mo):
    fetch = mo.ui.run_button(label="Fetch DC requirements history")
    mo.vstack([mo.md("*One CSV download, the full historical series in one file -- not a per-day loop.*"), fetch])
    return (fetch,)


@app.cell
def _(fetch, mo, neso, pd):
    mo.stop(not fetch.value, mo.md("*Click Fetch to load the DC requirements history.*"))

    _rows = neso.dc_requirements_forecast_history()
    requirements_df = pd.DataFrame(_rows)
    mo.md(f"Fetched **{len(requirements_df)}** rows.")
    return (requirements_df,)


@app.cell
def _(mo, requirements_df):
    mo.stop(requirements_df.empty, mo.md("*No data yet.*"))
    mo.vstack([mo.md("**Real columns (field names unconfirmed until now):**"), mo.ui.table(requirements_df.head(10))])
    return


@app.cell
def _(mo, requirements_df):
    mo.stop(requirements_df.empty)
    _service_types = (
        sorted(requirements_df["Service_Type"].dropna().unique().tolist())
        if "Service_Type" in requirements_df.columns
        else []
    )
    dc_high_value = mo.ui.dropdown(options=_service_types, label="Which Service_Type value is DC-High?")
    dc_low_value = mo.ui.dropdown(options=_service_types, label="Which Service_Type value is DC-Low?")
    latest_vintage_only = mo.ui.checkbox(
        value=True,
        label="Use only the latest Forecast_Created per Forecast_Target_Date (recommended -- otherwise multiple forecast revisions of the same day get counted as separate observations)",
    )
    mo.vstack([mo.hstack([dc_high_value, dc_low_value]), latest_vintage_only])
    return dc_high_value, dc_low_value, latest_vintage_only


@app.cell
def _(mo):
    mo.md(
        """
        ## The table: every (target date, EFA block, service) combination

        Reshaped from the real wide-by-EFA-column, long-by-service
        format: each of the six `EFA1`..`EFA6` columns becomes its own
        row, labelled via `efa_block_label_for_index()` -- confirmed
        block boundaries, not a guess about which column is which
        block (that mapping itself is an assumption, stated in the
        function's own docstring). Rows whose dates can't be parsed,
        or whose EFA value isn't numeric, are dropped, not silently
        zero-filled.
        """
    )
    return


@app.cell
def _(
    dc_high_value,
    dc_low_value,
    efa_block_label_for_index,
    latest_vintage_only,
    mo,
    pd,
    requirements_df,
):
    mo.stop(
        not all([dc_high_value.value, dc_low_value.value]),
        mo.md("*Pick both Service_Type mappings above.*"),
    )

    _df = requirements_df.copy()
    _df["_created"] = pd.to_datetime(_df["Forecast_Created"], dayfirst=True, errors="coerce")
    _df["_target"] = pd.to_datetime(_df["Forecast_Target_Date"], dayfirst=True, errors="coerce")
    _df = _df.dropna(subset=["_created", "_target"])

    if latest_vintage_only.value:
        _idx = _df.groupby(["_target", "Service_Type"])["_created"].idxmax()
        _df = _df.loc[_idx]

    _service_label = {dc_high_value.value: "DC-High", dc_low_value.value: "DC-Low"}

    _rows = []
    for _row in _df.to_dict("records"):
        _service = _row.get("Service_Type")
        if _service not in _service_label:
            continue
        for _efa_num in range(1, 7):
            _raw = _row.get(f"EFA{_efa_num}")
            _value = pd.to_numeric(_raw, errors="coerce")
            if pd.isna(_value):
                continue
            _rows.append({
                "target_date": _row["_target"],
                "forecast_created": _row["_created"],
                "efa_block": efa_block_label_for_index(_efa_num),
                "service": _service_label[_service],
                "requirement_mw": float(_value),
            })

    labelled_df = pd.DataFrame(_rows)
    mo.vstack([
        mo.md(f"**{len(labelled_df)}** (target date, EFA block, service) rows built from **{len(requirements_df)}** raw rows."),
        mo.ui.table(labelled_df, page_size=15),
    ])
    return (labelled_df,)


@app.cell
def _(mo):
    mo.md("## The chart: DC-High and DC-Low requirement, mean +/- spread, by EFA block")
    return


@app.cell
def _(EFA_BLOCKS, labelled_df, mo, pd, spread_chart):
    mo.stop(labelled_df.empty, mo.md("*No labelled rows to chart yet.*"))

    _block_order = [f"{s:02d}:00-{e:02d}:00" for s, e in EFA_BLOCKS]
    _high = labelled_df[labelled_df["service"] == "DC-High"]
    _grouped = _high.groupby("efa_block")["requirement_mw"].agg(["mean", "std", "count"]).reindex(_block_order)
    spread_chart(
        categories=list(_grouped.index),
        means=[round(v, 1) if pd.notna(v) else 0.0 for v in _grouped["mean"]],
        std_devs=[round(v, 1) if pd.notna(v) else 0.0 for v in _grouped["std"]],
        counts=[int(v) if pd.notna(v) else 0 for v in _grouped["count"]],
        title="DC-High requirement by EFA block",
        x_label="EFA block",
        y_label="DC-High requirement (MW)",
    )
    return


@app.cell
def _(EFA_BLOCKS, labelled_df, mo, spread_chart, pd):
    mo.stop(labelled_df.empty)

    _block_order = [f"{s:02d}:00-{e:02d}:00" for s, e in EFA_BLOCKS]
    _low = labelled_df[labelled_df["service"] == "DC-Low"]
    _grouped = _low.groupby("efa_block")["requirement_mw"].agg(["mean", "std", "count"]).reindex(_block_order)
    spread_chart(
        categories=list(_grouped.index),
        means=[round(v, 1) if pd.notna(v) else 0.0 for v in _grouped["mean"]],
        std_devs=[round(v, 1) if pd.notna(v) else 0.0 for v in _grouped["std"]],
        counts=[int(v) if pd.notna(v) else 0 for v in _grouped["count"]],
        title="DC-Low requirement by EFA block",
        x_label="EFA block",
        y_label="DC-Low requirement (MW)",
    )
    return


@app.cell
def _(mo):
    mo.md(
        """
        ## Section 2 -- does system inertia explain the variation?

        The hypothesis this notebook was built to check: if largest
        losses and other-response levels turn out to be relatively
        fixed, inertia would be the main *varying* driver of DC
        requirement. Tested here with **outturn** inertia against
        **outturn** DC requirement (both from history/actuals) --
        an honest way to check correlation, not a forecast-vintage
        match.

        Resource ID below is the latest one independently confirmed
        (2024-2025) -- if a 2025-2026 resource exists (very likely,
        given the consistent yearly pattern), swap it in once you have
        the ID.
        """
    )
    return


@app.cell
def _(SYSTEM_INERTIA_RESOURCES, mo):
    inertia_year = mo.ui.dropdown(
        options=list(SYSTEM_INERTIA_RESOURCES.keys()), value="2024-2025", label="System inertia year"
    )
    fetch_inertia = mo.ui.run_button(label="Fetch inertia")
    mo.hstack([inertia_year, fetch_inertia])
    return fetch_inertia, inertia_year


@app.cell
def _(SYSTEM_INERTIA_RESOURCES, fetch_inertia, inertia_year, mo, neso, pd):
    mo.stop(not fetch_inertia.value, mo.md("*Pick a year and click Fetch.*"))

    _rows = neso.system_inertia(SYSTEM_INERTIA_RESOURCES[inertia_year.value], limit=5000)
    inertia_df = pd.DataFrame(_rows)
    mo.md(f"Fetched **{len(inertia_df)}** inertia rows for {inertia_year.value}.")
    return (inertia_df,)


@app.cell
def _(inertia_df, mo):
    mo.stop(inertia_df.empty, mo.md("*No inertia data yet.*"))
    mo.vstack([mo.md("**Real inertia columns (unconfirmed until now):**"), mo.ui.table(inertia_df.head(10))])
    return


@app.cell
def _(inertia_df, mo):
    mo.stop(inertia_df.empty)
    inertia_datetime_field = mo.ui.dropdown(options=list(inertia_df.columns), label="Datetime field (inertia)")
    inertia_value_field = mo.ui.dropdown(options=list(inertia_df.columns), label="Inertia value field")
    inertia_bin_width = mo.ui.number(value=20, label="Inertia bin width (GVA.s presumed -- adjust once you see it)")
    mo.hstack([inertia_datetime_field, inertia_value_field, inertia_bin_width])
    return inertia_bin_width, inertia_datetime_field, inertia_value_field


@app.cell
def _(mo):
    mo.md(
        """
        ### The table: DC-High requirement joined against outturn inertia

        Filtered to `service == "DC-High"` first, then joined on
        `target_date` (day-level, not exact EFA block -- both
        datasets' periods don't necessarily align finely enough to
        guarantee a clean block-level join without checking the real
        field shapes first). Worth refining once you've seen the real
        data.
        """
    )
    return


@app.cell
def _(
    inertia_datetime_field,
    inertia_df,
    inertia_value_field,
    labelled_df,
    mo,
    pd,
):
    mo.stop(
        not all([inertia_datetime_field.value, inertia_value_field.value]),
        mo.md("*Pick both inertia field mappings above.*"),
    )

    _inertia_by_day = {}
    for _row in inertia_df.to_dict("records"):
        _parsed = pd.to_datetime(_row.get(inertia_datetime_field.value), dayfirst=True, errors="coerce")
        if pd.isna(_parsed):
            continue
        _value = pd.to_numeric(_row.get(inertia_value_field.value), errors="coerce")
        if pd.isna(_value):
            continue
        _inertia_by_day.setdefault(_parsed.date().isoformat(), []).append(float(_value))

    _dc_high_only = labelled_df[labelled_df["service"] == "DC-High"]

    _joined = []
    for _row in _dc_high_only.to_dict("records"):
        _day_key = _row["target_date"].date().isoformat()
        _inertia_values = _inertia_by_day.get(_day_key)
        if not _inertia_values:
            continue
        _joined.append({
            "target_date": _row["target_date"], "efa_block": _row["efa_block"],
            "requirement_mw": _row["requirement_mw"],
            "inertia": sum(_inertia_values) / len(_inertia_values),  # daily average -- see the note above on join granularity
        })

    inertia_joined_df = pd.DataFrame(_joined)
    mo.vstack([
        mo.md(f"**{len(inertia_joined_df)}** of {len(_dc_high_only)} DC-High rows matched an inertia value."),
        mo.ui.table(inertia_joined_df, page_size=15),
    ])
    return (inertia_joined_df,)


@app.cell
def _(mo):
    mo.md("### The chart: DC-High requirement spread, by inertia bucket")
    return


@app.cell
def _(inertia_bin_width, inertia_joined_df, mo, spread_by_bin, spread_chart):
    mo.stop(inertia_joined_df.empty, mo.md("*No matched rows to chart yet.*"))

    _spread = spread_by_bin(
        inertia_joined_df["inertia"].tolist(), inertia_joined_df["requirement_mw"].tolist(),
        bin_width=float(inertia_bin_width.value),
    )
    spread_chart(
        categories=_spread["bin_labels"],
        means=_spread["means"],
        std_devs=_spread["std_devs"],
        counts=_spread["counts"],
        title="DC-High requirement by inertia bucket -- lower inertia, higher requirement?",
        x_label="Inertia bucket (scale unconfirmed)",
        y_label="DC-High requirement (mean +/- 1 std dev, MW)",
    )
    return


@app.cell
def _(mo):
    mo.md(
        """
        ## Section 3 -- does DC requirement move with largest secured loss, independent of inertia?

        The remaining hypothesis before treating inertia as the sole
        driver: does requirement volume also track the size of the
        largest credible loss NESO must secure against, separately
        from inertia's own effect?

        **Confirmed directly from NESO's own Frequency Risk and
        Control Report (a real published document, not inferred):**
        *"Current policy focuses on securing BMU-only events with
        their consequential RoCoF loss"* -- and *"As our largest loss
        size increases, with sites such as Hinkley-C power station
        connecting to the network in the future, we will see a
        significant increase in DC requirements to cover a larger
        individual loss."* NESO states directly that requirement
        tracks largest loss size -- this section checks that against
        real data, and specifically checks whether it holds up once
        inertia is held roughly constant, not just in the raw,
        unconditional relationship.

        **Direction mapping, as given:** an import loss (an importing
        interconnector, or a domestic generator like SIZB, tripping)
        causes a low-frequency event -> DC-Low. An export loss (an
        exporting interconnector tripping) causes a high-frequency
        event -> DC-High.

        **No confirmed "largest loss" dataset exists** (checked --
        see `docs/adr/0015`). Reconstructed here instead from real
        per-BMU metered output (B1610). **B1610 is a per-settlement-
        period endpoint** (`settlementDate` + `settlementPeriod`,
        confirmed directly against Elexon's own API documentation
        page and a real confirmed request URL -- not the `from`/`to`
        bulk-range shape an earlier version of this notebook wrongly
        assumed, sourced at the time from a third-party wrapper's
        convenience parameter names rather than the actual API docs).
        A full day now costs 48 requests, not one -- see the cost note
        and days-to-fetch control below, same pattern as the
        acceptances-heavy notebooks elsewhere in this project.
        **B1610 also has a 5-working-day settlement lag** -- fine for
        this historical correlation check, not usable for a live
        decision.

        SIZB and interconnectors are identified the same way batteries
        were identified earlier in this project: an ID-pattern match
        against `bm_units_reference()`, with the actual matches shown
        below for you to check -- not trusted blindly, same discipline
        as everywhere else this pattern has been used.
        """
    )
    return


@app.cell
def _(mo):
    siz_pattern = mo.ui.text(value="SIZB", label="SIZB BM Unit ID pattern (regex)")
    interconnector_pattern = mo.ui.text(
        value="", label="Interconnector BM Unit ID pattern (regex, e.g. ^I_)", placeholder="unconfirmed -- try one and check the matches"
    )
    fetch_losses = mo.ui.run_button(label="Fetch BMU reference + B1610")
    mo.vstack([mo.hstack([siz_pattern, interconnector_pattern]), fetch_losses])
    return fetch_losses, interconnector_pattern, siz_pattern


@app.cell
def _(elexon, fetch_losses, mo, pd):
    mo.stop(not fetch_losses.value, mo.md("*Enter both patterns and click Fetch.*"))

    _rows = elexon.bm_units_reference()
    bmunits_df = pd.DataFrame(_rows)
    mo.md(f"Fetched **{len(bmunits_df)}** BM unit reference rows.")
    return (bmunits_df,)


@app.cell
def _(bmunits_df, mo):
    mo.stop(bmunits_df.empty, mo.md("*No BM unit reference data yet.*"))
    ref_id_field = mo.ui.dropdown(options=list(bmunits_df.columns), label="BM unit ID field (reference)")
    ref_id_field
    return (ref_id_field,)


@app.cell
def _(
    bmunits_df,
    filter_bmus_by_id_pattern,
    interconnector_pattern,
    mo,
    ref_id_field,
    siz_pattern,
):
    mo.stop(not ref_id_field.value, mo.md("*Pick the BM unit ID field above.*"))

    _siz_matches = filter_bmus_by_id_pattern(bmunits_df.to_dict("records"), id_field=ref_id_field.value, pattern=siz_pattern.value)
    _interconnector_matches = (
        filter_bmus_by_id_pattern(bmunits_df.to_dict("records"), id_field=ref_id_field.value, pattern=interconnector_pattern.value)
        if interconnector_pattern.value
        else set()
    )
    candidate_bmu_ids = _siz_matches | _interconnector_matches

    mo.vstack([
        mo.md(
            f"**{len(candidate_bmu_ids)} candidate loss-source BM units matched** "
            f"({len(_siz_matches)} SIZB, {len(_interconnector_matches)} interconnector pattern). "
            f"**Check this list** -- an ID pattern can catch units it shouldn't, same risk as the "
            f"battery-identification work earlier in this project."
        ),
        mo.ui.table(bmunits_df[bmunits_df[ref_id_field.value].isin(candidate_bmu_ids)]) if candidate_bmu_ids else mo.md("*No matches.*"),
    ])
    return (candidate_bmu_ids,)


@app.cell
def _(fetch_losses, labelled_df, mo):
    mo.stop(not fetch_losses.value, mo.md("*Click Fetch above.*"))
    mo.stop(labelled_df.empty, mo.md("*Fetch DC requirements in Section 1 first.*"))

    _n_available = labelled_df["target_date"].dt.date.nunique()
    b1610_days_to_fetch = mo.ui.slider(1, min(_n_available, 14), value=min(3, _n_available), label="Days of B1610 to fetch (start small!)")
    b1610_days_to_fetch
    return (b1610_days_to_fetch,)


@app.cell
def _(b1610_days_to_fetch, mo):
    mo.md(
        f"*B1610 is confirmed per-settlement-period (`docs/adr/0016`'s correction), not a bulk date-range "
        f"call -- fetching **{b1610_days_to_fetch.value} day(s)** is **{b1610_days_to_fetch.value * 48} requests** "
        f"(one per period, all BM units per call, filtered to candidates afterward -- cheaper in request count "
        f"than filtering server-side per candidate BM unit would be). Raise the slider once a small run looks right.*"
    )
    return


@app.cell
def _(b1610_days_to_fetch, elexon, fetch_losses, labelled_df, mo, pd):
    mo.stop(not fetch_losses.value, mo.md("*Click Fetch above.*"))
    mo.stop(labelled_df.empty, mo.md("*Fetch DC requirements in Section 1 first -- the date range comes from there.*"))

    _days = sorted(labelled_df["target_date"].dt.date.unique())[: b1610_days_to_fetch.value]

    _rows = []
    for _day in _days:
        for _row in elexon.actual_generation_per_bmu_for_day(_day):
            _row["_day"] = _day.isoformat()
            _rows.append(_row)

    b1610_df = pd.DataFrame(_rows)
    mo.md(f"Fetched **{len(b1610_df)}** B1610 rows across **{len(_days)}** day(s).")
    return (b1610_df,)


@app.cell
def _(b1610_df, mo):
    mo.stop(b1610_df.empty, mo.md("*No B1610 data yet.*"))
    mo.vstack([mo.md("**Real B1610 columns (unconfirmed until now):**"), mo.ui.table(b1610_df.head(10))])
    return


@app.cell
def _(b1610_df, mo):
    mo.stop(b1610_df.empty)
    b1610_bmu_field = mo.ui.dropdown(options=list(b1610_df.columns), label="BM unit ID field (B1610)")
    b1610_value_field = mo.ui.dropdown(options=list(b1610_df.columns), label="Metered value field (B1610)")
    mo.hstack([b1610_bmu_field, b1610_value_field])
    return b1610_bmu_field, b1610_value_field


@app.cell
def _(mo):
    mo.md(
        """
        ### The table: largest import and export exposure, by day

        Rows filtered to the matched candidate BM units, classified
        Import/Export by sign (`direction_from_sign()` -- unconfirmed
        for interconnectors specifically, see Section 3's intro), then
        the largest value per day per direction
        (`largest_value_by_group()`) becomes that day's loss-exposure
        proxy. Day comes from the fetch loop (`_day`, attached when
        each day was fetched), not a parsed B1610 date field -- the
        same robust pattern used for the demand-forecast and wind
        notebooks, avoiding reliance on an unconfirmed date field
        entirely rather than adding one more dropdown for it.
        """
    )
    return


@app.cell
def _(
    b1610_bmu_field,
    b1610_df,
    b1610_value_field,
    candidate_bmu_ids,
    direction_from_sign,
    largest_value_by_group,
    mo,
    pd,
):
    mo.stop(
        not all([b1610_bmu_field.value, b1610_value_field.value]),
        mo.md("*Pick both B1610 field mappings above.*"),
    )

    _filtered = []
    for _row in b1610_df.to_dict("records"):
        if _row.get(b1610_bmu_field.value) not in candidate_bmu_ids:
            continue
        _value = pd.to_numeric(_row.get(b1610_value_field.value), errors="coerce")
        if pd.isna(_value):
            continue
        _filtered.append({
            "day": _row["_day"],
            "direction": direction_from_sign(float(_value)),
            "value": abs(float(_value)),
        })

    _import_rows = [r for r in _filtered if r["direction"] == "Import"]
    _export_rows = [r for r in _filtered if r["direction"] == "Export"]
    import_loss_by_day = largest_value_by_group(_import_rows, group_field="day", value_field="value")
    export_loss_by_day = largest_value_by_group(_export_rows, group_field="day", value_field="value")

    mo.md(
        f"**{len(candidate_bmu_ids)}** candidate units, **{len(_filtered)}** matched B1610 rows -- "
        f"**{len(import_loss_by_day)}** days with an import-direction reading, "
        f"**{len(export_loss_by_day)}** days with an export-direction reading."
    )
    return export_loss_by_day, import_loss_by_day


@app.cell
def _(mo):
    mo.md("### The chart: DC-Low vs largest import loss -- unconditional, then with inertia held roughly constant")
    return


@app.cell
def _(EFA_BLOCKS, import_loss_by_day, inertia_joined_df, labelled_df, mo, pd, spread_by_bin, spread_chart):
    mo.stop(inertia_joined_df.empty or not import_loss_by_day, mo.md("*Run Section 2 (inertia) and the B1610 build cell above first.*"))

    _dc_low = labelled_df[labelled_df["service"] == "DC-Low"].copy()
    _dc_low["day"] = _dc_low["target_date"].dt.date.astype(str)
    # reuse inertia_joined_df's day->inertia mapping (it's DC-High rows, but inertia is a system-wide value, not service-specific)
    _inertia_by_day = dict(zip(inertia_joined_df["target_date"].dt.date.astype(str), inertia_joined_df["inertia"]))

    _rows = []
    for _row in _dc_low.to_dict("records"):
        _loss = import_loss_by_day.get(_row["day"], {}).get("max_value")
        _inertia = _inertia_by_day.get(_row["day"])
        if _loss is None or _inertia is None:
            continue
        _rows.append({"requirement_mw": _row["requirement_mw"], "import_loss": _loss, "inertia": _inertia})

    dc_low_loss_df = pd.DataFrame(_rows)
    mo.stop(dc_low_loss_df.empty, mo.md("*No matched rows -- check the B1610 date range covers the DC requirements dates.*"))

    _unconditional = spread_by_bin(dc_low_loss_df["import_loss"].tolist(), dc_low_loss_df["requirement_mw"].tolist(), bin_width=100.0)
    _fig_unconditional = spread_chart(
        categories=_unconditional["bin_labels"], means=_unconditional["means"], std_devs=_unconditional["std_devs"],
        counts=_unconditional["counts"], title="DC-Low requirement by import-loss bucket (unconditional)",
        x_label="Largest import loss (MW)", y_label="DC-Low requirement (MW)",
    )

    _lo, _hi = dc_low_loss_df["inertia"].quantile(0.33), dc_low_loss_df["inertia"].quantile(0.67)
    _controlled = dc_low_loss_df[(dc_low_loss_df["inertia"] >= _lo) & (dc_low_loss_df["inertia"] <= _hi)]
    _controlled_spread = spread_by_bin(_controlled["import_loss"].tolist(), _controlled["requirement_mw"].tolist(), bin_width=100.0)
    _fig_controlled = spread_chart(
        categories=_controlled_spread["bin_labels"], means=_controlled_spread["means"], std_devs=_controlled_spread["std_devs"],
        counts=_controlled_spread["counts"],
        title=f"DC-Low requirement by import-loss bucket (inertia held to middle tercile, n={len(_controlled)})",
        x_label="Largest import loss (MW)", y_label="DC-Low requirement (MW)",
    )

    mo.vstack([_fig_unconditional, _fig_controlled])
    return


@app.cell
def _(mo):
    mo.md("### The chart: DC-High vs largest export loss -- unconditional, then with inertia held roughly constant")
    return


@app.cell
def _(EFA_BLOCKS, export_loss_by_day, inertia_joined_df, labelled_df, mo, pd, spread_by_bin, spread_chart):
    mo.stop(inertia_joined_df.empty or not export_loss_by_day, mo.md("*Run Section 2 (inertia) and the B1610 build cell above first.*"))

    _dc_high = labelled_df[labelled_df["service"] == "DC-High"].copy()
    _dc_high["day"] = _dc_high["target_date"].dt.date.astype(str)
    _inertia_by_day = dict(zip(inertia_joined_df["target_date"].dt.date.astype(str), inertia_joined_df["inertia"]))

    _rows = []
    for _row in _dc_high.to_dict("records"):
        _loss = export_loss_by_day.get(_row["day"], {}).get("max_value")
        _inertia = _inertia_by_day.get(_row["day"])
        if _loss is None or _inertia is None:
            continue
        _rows.append({"requirement_mw": _row["requirement_mw"], "export_loss": _loss, "inertia": _inertia})

    dc_high_loss_df = pd.DataFrame(_rows)
    mo.stop(dc_high_loss_df.empty, mo.md("*No matched rows -- check the B1610 date range covers the DC requirements dates, and that any exporting interconnector rows were actually found.*"))

    _unconditional = spread_by_bin(dc_high_loss_df["export_loss"].tolist(), dc_high_loss_df["requirement_mw"].tolist(), bin_width=100.0)
    _fig_unconditional = spread_chart(
        categories=_unconditional["bin_labels"], means=_unconditional["means"], std_devs=_unconditional["std_devs"],
        counts=_unconditional["counts"], title="DC-High requirement by export-loss bucket (unconditional)",
        x_label="Largest export loss (MW)", y_label="DC-High requirement (MW)",
    )

    _lo, _hi = dc_high_loss_df["inertia"].quantile(0.33), dc_high_loss_df["inertia"].quantile(0.67)
    _controlled = dc_high_loss_df[(dc_high_loss_df["inertia"] >= _lo) & (dc_high_loss_df["inertia"] <= _hi)]
    _controlled_spread = spread_by_bin(_controlled["export_loss"].tolist(), _controlled["requirement_mw"].tolist(), bin_width=100.0)
    _fig_controlled = spread_chart(
        categories=_controlled_spread["bin_labels"], means=_controlled_spread["means"], std_devs=_controlled_spread["std_devs"],
        counts=_controlled_spread["counts"],
        title=f"DC-High requirement by export-loss bucket (inertia held to middle tercile, n={len(_controlled)})",
        x_label="Largest export loss (MW)", y_label="DC-High requirement (MW)",
    )

    mo.vstack([_fig_unconditional, _fig_controlled])
    return


if __name__ == "__main__":
    app.run()
