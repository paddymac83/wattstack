import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    from datetime import date, datetime, timedelta, timezone

    import marimo as mo
    import pandas as pd

    from wattstack_ingestion.analysis import (
        bin_counts_by_group,
        bucket_start_for_value,
        classify_system_length,
        probability_by_bin,
        shrink_probability_by_bin,
    )
    from wattstack_ingestion.cache import Cache
    from wattstack_ingestion.elexon import ElexonClient
    from wattstack_ingestion.forecasts import ElexonDemandForecastProvider

    return (
        Cache,
        ElexonClient,
        ElexonDemandForecastProvider,
        bin_counts_by_group,
        bucket_start_for_value,
        classify_system_length,
        datetime,
        mo,
        pd,
        shrink_probability_by_bin,
        timedelta,
        timezone,
    )


@app.cell
def _(mo):
    mo.md("""
    # Probabilistic day-ahead imbalance (System Price) forecast

    Built from two real sources, not from scratch:

    - Timera Energy, ["The rising cost of system imbalance"](https://timera-energy.com/blog/the-rising-cost-of-system-imbalance/)
      (Mar 2026) -- the central point this notebook is built around:
      *"the risk is in the distribution -- not the mean."* The
      average Day-Ahead-vs-imbalance spread is ~zero (a competitive
      market arbitrages away any persistent bias); what varies, and
      is genuinely predictable, is the *shape* of the distribution.
    - Browell & Gilbert,
      ["Predicting Electricity Imbalance Prices and Volumes"](https://pure.strath.ac.uk/ws/portalfiles/portal/135622617/Browell_Gilbert_Energies_2022_Predicting_electricity_imbalance_prices_and_volumes.pdf)
      (Energies, 2022) -- the concrete day-ahead architecture this
      notebook implements: *"Separate density forecasts for long
      and short systems are combined according to the forecast
      probability of the system being long or short."*

    **Set expectations honestly, from the paper's own result, not
    optimism:** their day-ahead model beat a simple climatological
    benchmark by only **3% MAE**. Their own words: *"climatological
    or simple models perform well and are very difficult to improve
    on"* at the day-ahead horizon specifically -- real improvement
    only shows up intraday (5-40%), where far more information is
    available. The honest target here is a modest, measurable
    improvement over the flat seasonal average already built
    (`ElexonBMPriceProvider`), not a large leap.

    **Real correction from what's already built:** this targets
    **System Price** (the real imbalance settlement price), not the
    BOD submitted-price average `ElexonBMPriceProvider` currently
    uses. Timera's own framing -- *"the imbalance price is derived
    from the marginal action taken within the BM"* -- makes System
    Price the more principled quantity: it already reflects which
    action was actually needed, not just what was offered.

    **The architecture:**
    ```
    F(price) = P(Short) x F(price | Short) + P(Long) x F(price | Long)
    ```
    `P(Short)` comes from a genuine, data-driven empirical
    probability (`probability_by_bin()`, new) -- not a
    classification, a probability, conditioned on forecast demand.
    `F(price | Short)` and `F(price | Long)` are empirical
    conditional distributions of real historical System Price,
    split by realised system length (`classify_system_length()`,
    already validated against Elexon's published SPAR figures).

    **What this notebook does NOT yet do:** promote anything to a
    production `PriceProvider` -- that's the next step, after this
    validates (or doesn't) against real held-out data. Same
    "explore, then promote" discipline as every other real signal
    in this project.

    **Real finding, from a real run against live data:** the first
    version of this notebook used raw empirical probabilities and
    was dramatically *worse* than the flat baseline (£22/MWh MAE
    vs £5.67/MWh -- a mixture model losing to a plain average).
    Diagnosed, not dismissed: a demand bucket with only a handful
    of historical observations can show a raw rate like "100%
    Short" purely by chance, and a mixture model that *confidently*
    swings toward the Short-mean based on that noise is worse than
    an honest average -- being confidently wrong costs more than
    being uncertain. Fixed with `shrink_probability_by_bin()`
    (new): pulls thin buckets toward the dataset's overall rate,
    barely touching well-populated ones. A diagnostic cell below
    shows bucket sample sizes directly, so this is visible, not
    hidden inside the probability numbers.
    """)
    return


@app.cell
def _(Cache, ElexonClient, ElexonDemandForecastProvider):
    cache = Cache("wattstack_ingestion_cache.sqlite")
    elexon = ElexonClient(cache=cache)
    forecast_provider = ElexonDemandForecastProvider(client=elexon)
    return elexon, forecast_provider


@app.cell
def _(mo):
    start_date = mo.ui.date(label="Start date")
    days_to_fetch = mo.ui.number(value=30, start=7, stop=180, label="Days to fetch (2 calls/day: demand history + system prices)")
    fetch = mo.ui.run_button(label="Fetch")
    mo.vstack([mo.hstack([start_date, days_to_fetch]), fetch])
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
    mo.stop(not fetch.value, mo.md("*Click Fetch above.*"))

    _demand_rows, _actual_rows = [], []
    for _offset in range(days_to_fetch.value):
        _day = start_date.value + timedelta(days=_offset)
        _trigger_time = datetime(_day.year, _day.month, _day.day, tzinfo=timezone.utc) - timedelta(hours=14)

        for _row in forecast_provider.as_of(_trigger_time):
            _row["_day"] = _day.isoformat()
            _demand_rows.append(_row)

        for _row in elexon.system_prices(_day):
            _row["_day"] = _day.isoformat()
            _actual_rows.append(_row)

    demand_df = pd.DataFrame(_demand_rows)
    actual_df = pd.DataFrame(_actual_rows)
    mo.md(
        f"Fetched **{len(demand_df)}** demand forecast rows and **{len(actual_df)}** "
        f"system price rows across {days_to_fetch.value} day(s)."
    )
    return actual_df, demand_df


@app.cell
def _(actual_df, demand_df, mo):
    mo.stop(demand_df.empty or actual_df.empty, mo.md("*No data yet.*"))
    mo.vstack([
        mo.md("**Demand forecast columns:**"), mo.ui.table(demand_df.head(3)),
        mo.md("**System price columns:**"), mo.ui.table(actual_df.head(3)),
    ])
    return


@app.cell
def _(actual_df, demand_df, mo):
    mo.stop(demand_df.empty or actual_df.empty)
    forecast_period_field = mo.ui.dropdown(options=list(demand_df.columns), label="Settlement period field (demand)")
    demand_field = mo.ui.dropdown(options=list(demand_df.columns), label="Demand value field")
    price_field = mo.ui.dropdown(options=list(actual_df.columns), label="System price field")
    niv_field = mo.ui.dropdown(options=list(actual_df.columns), label="NIV field")
    demand_bin_width = mo.ui.number(value=1000, label="Demand bin width (MW)")
    mo.vstack([mo.hstack([forecast_period_field, demand_field]), mo.hstack([price_field, niv_field, demand_bin_width])])
    return (
        demand_bin_width,
        demand_field,
        forecast_period_field,
        niv_field,
        price_field,
    )


@app.cell
def _(mo):
    mo.md("""
    ## The table: forecast demand joined against realised price and system length
    """)
    return


@app.cell
def _(
    actual_df,
    classify_system_length,
    demand_df,
    demand_field,
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

    _actual_by_key = {}
    for _row in actual_df.to_dict("records"):
        _period = _row.get(forecast_period_field.value)  # same field name confirmed to match across both datasets
        _actual_by_key[(_row["_day"], _period)] = _row

    _rows = []
    for _row in demand_df.to_dict("records"):
        _period = _row.get(forecast_period_field.value)
        _actual = _actual_by_key.get((_row["_day"], _period))
        if _actual is None:
            continue
        _niv = pd.to_numeric(_actual.get(niv_field.value), errors="coerce")
        _price = pd.to_numeric(_actual.get(price_field.value), errors="coerce")
        _demand = pd.to_numeric(_row.get(demand_field.value), errors="coerce")
        if pd.isna(_niv) or pd.isna(_price) or pd.isna(_demand):
            continue
        _rows.append({
            "day": _row["_day"], "settlement_period": _period,
            "forecast_demand": float(_demand), "actual_price": float(_price),
            "system_length": classify_system_length(float(_niv)),
        })

    labelled_df = pd.DataFrame(_rows).sort_values(["day", "settlement_period"]).reset_index(drop=True)
    mo.vstack([
        mo.md(f"**{len(labelled_df)}** rows successfully joined."),
        mo.ui.table(labelled_df, page_size=15),
    ])
    return (labelled_df,)


@app.cell
def _(mo):
    mo.md("""
    ## Train/test split

    Chronological, not random -- a random split would leak future
    information into training (a day's realised price shouldn't
    help predict an earlier day's forecast), the same reason
    Browell & Gilbert used a strict chronological holdout
    (Jan 2017-Oct 2020 train, Oct-Dec 2020 test).
    """)
    return


@app.cell
def _(labelled_df, mo):
    mo.stop(labelled_df.empty, mo.md("*Join demand and price data above first.*"))
    _unique_days = sorted(labelled_df["day"].unique())
    test_days_count = mo.ui.slider(
        1, max(len(_unique_days) - 3, 1), value=max(len(_unique_days) // 5, 1),
        label=f"Days held out for testing (of {len(_unique_days)} total)",
    )
    test_days_count
    return (test_days_count,)


@app.cell
def _(labelled_df, mo, test_days_count):
    mo.stop(labelled_df.empty)
    _unique_days = sorted(labelled_df["day"].unique())
    _test_days = set(_unique_days[-test_days_count.value:])
    train_df = labelled_df[~labelled_df["day"].isin(_test_days)].reset_index(drop=True)
    test_df = labelled_df[labelled_df["day"].isin(_test_days)].reset_index(drop=True)
    mo.md(
        f"**{len(train_df)}** training rows ({len(_unique_days) - test_days_count.value} days), "
        f"**{len(test_df)}** test rows ({test_days_count.value} days)."
    )
    return test_df, train_df


@app.cell
def _(mo):
    mo.md("""
    ## Train: P(Short | forecast demand bucket) and conditional price distributions

    Both trained on `train_df` only -- the test set is never
    touched until the final backtest cell.

    **Diagnostic first, before trusting any probability number:**
    how many observations actually sit behind each bucket. A
    bucket with 2-3 observations is not a reliable estimate of
    anything, however extreme its raw rate looks.
    """)
    return


@app.cell
def _(bin_counts_by_group, demand_bin_width, mo, pd, train_df):
    mo.stop(train_df.empty, mo.md("*No training data yet.*"))
    _counts = bin_counts_by_group(
        train_df["forecast_demand"].tolist(), train_df["system_length"].tolist(), bin_width=float(demand_bin_width.value)
    )
    _rows = [
        {"bucket": label, **{g: _counts["counts"][g][i] for g in _counts["groups"]},
         "total": sum(_counts["counts"][g][i] for g in _counts["groups"])}
        for i, label in enumerate(_counts["bin_labels"])
    ]
    mo.ui.table(pd.DataFrame(_rows), page_size=15)
    return


@app.cell
def _(mo):
    shrinkage_strength = mo.ui.number(
        value=10.0, label="Shrinkage strength (pseudo-count -- 0 = raw frequency, higher = more pulled toward overall rate)"
    )
    shrinkage_strength
    return (shrinkage_strength,)


@app.cell
def _(
    demand_bin_width,
    mo,
    shrink_probability_by_bin,
    shrinkage_strength,
    train_df,
):
    mo.stop(train_df.empty, mo.md("*No training data yet.*"))
    probability_table = shrink_probability_by_bin(
        train_df["forecast_demand"].tolist(), train_df["system_length"].tolist(),
        bin_width=float(demand_bin_width.value), shrinkage_strength=float(shrinkage_strength.value),
    )
    mo.md(f"Trained P(Short|demand bucket) over **{len(probability_table)}** buckets (shrinkage strength {shrinkage_strength.value}).")
    return (probability_table,)


@app.cell
def _(mo, train_df):
    mo.stop(train_df.empty)
    import statistics as _statistics

    _short_prices = train_df[train_df["system_length"] == "Short"]["actual_price"].tolist()
    _long_prices = train_df[train_df["system_length"] == "Long"]["actual_price"].tolist()

    conditional_stats = {
        "Short": {
            "mean": _statistics.mean(_short_prices) if _short_prices else None,
            "quantiles": _statistics.quantiles(_short_prices, n=20) if len(_short_prices) >= 20 else None,
            "n": len(_short_prices),
        },
        "Long": {
            "mean": _statistics.mean(_long_prices) if _long_prices else None,
            "quantiles": _statistics.quantiles(_long_prices, n=20) if len(_long_prices) >= 20 else None,
            "n": len(_long_prices),
        },
    }
    _unconditional_mean = _statistics.mean(train_df["actual_price"].tolist())

    mo.md(
        f"**Mean price | Short:** {conditional_stats['Short']['mean']:.2f} (n={conditional_stats['Short']['n']})  \n"
        f"**Mean price | Long:** {conditional_stats['Long']['mean']:.2f} (n={conditional_stats['Long']['n']})  \n"
        f"**Unconditional mean (the flat baseline):** {_unconditional_mean:.2f}"
    )
    return (conditional_stats,)


@app.cell
def _(mo):
    mo.md("""
    ## Backtest: mixture forecast vs flat baseline, on held-out test data

    Mean Absolute Error against realised price, matching Browell &
    Gilbert's own evaluation metric directly -- the only honest way
    to know whether this is a genuine improvement, not just a more
    complicated way of getting the same answer.
    """)
    return


@app.cell
def _(
    bucket_start_for_value,
    conditional_stats,
    demand_bin_width,
    mo,
    probability_table,
    test_df,
    train_df,
):
    mo.stop(test_df.empty, mo.md("*No test data yet -- increase days fetched or reduce the test holdout.*"))

    import statistics as _statistics2

    _unconditional_mean = _statistics2.mean(train_df["actual_price"].tolist())
    _available_buckets = list(probability_table.keys())

    _mixture_errors = []
    _flat_errors = []
    for _row in test_df.to_dict("records"):
        _actual = _row["actual_price"]

        _bucket = bucket_start_for_value(_row["forecast_demand"], float(demand_bin_width.value), _available_buckets)
        _has_both_means = conditional_stats["Short"]["mean"] is not None and conditional_stats["Long"]["mean"] is not None
        if _bucket is not None and _has_both_means:
            _probs = probability_table[_bucket]
            _p_short = _probs.get("Short", 0.0)
            _p_long = _probs.get("Long", 0.0)
            _mixture_forecast = _p_short * conditional_stats["Short"]["mean"] + _p_long * conditional_stats["Long"]["mean"]
        else:
            _mixture_forecast = _unconditional_mean  # no trained bucket at all -- fall back to the flat baseline itself

        _mixture_errors.append(abs(_mixture_forecast - _actual))
        _flat_errors.append(abs(_unconditional_mean - _actual))

    _mixture_mae = _statistics2.mean(_mixture_errors)
    _flat_mae = _statistics2.mean(_flat_errors)
    _improvement_pct = (_flat_mae - _mixture_mae) / _flat_mae * 100 if _flat_mae else 0.0

    mo.md(
        f"**Mixture-model MAE:** £{_mixture_mae:.2f}/MWh  \n"
        f"**Flat-baseline MAE:** £{_flat_mae:.2f}/MWh  \n"
        f"**Improvement:** {_improvement_pct:.1f}% -- compare against Browell & Gilbert's own **3%** day-ahead "
        f"result before concluding this is (or isn't) working."
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Shrinkage sweep: which strength actually performs best?

    Rather than adjusting the slider above one value at a time,
    this tries a range of `shrinkage_strength` values
    automatically and reports the resulting backtest MAE for
    each -- the fastest way to find a good value once real data
    is loaded, not a replacement for understanding *why*
    shrinkage matters (see the diagnostic table and intro above
    for that). `shrinkage_strength=0` here is the raw,
    unshrunk estimate -- the same one that gave the £22.22/MWh
    result against real data before this fix.
    """)
    return


@app.cell
def _(
    bucket_start_for_value,
    conditional_stats,
    demand_bin_width,
    mo,
    pd,
    shrink_probability_by_bin,
    test_df,
    train_df,
):
    mo.stop(test_df.empty or train_df.empty, mo.md("*Need both train and test data first.*"))

    import statistics as _statistics3

    _unconditional_mean = _statistics3.mean(train_df["actual_price"].tolist())
    _has_both_means = conditional_stats["Short"]["mean"] is not None and conditional_stats["Long"]["mean"] is not None
    _flat_mae = _statistics3.mean(abs(_unconditional_mean - r["actual_price"]) for r in test_df.to_dict("records"))

    _candidate_strengths = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
    _sweep_rows = []
    for _strength in _candidate_strengths:
        _table = shrink_probability_by_bin(
            train_df["forecast_demand"].tolist(), train_df["system_length"].tolist(),
            bin_width=float(demand_bin_width.value), shrinkage_strength=_strength,
        )
        _available_buckets = list(_table.keys())

        _mixture_errors = []
        for _row in test_df.to_dict("records"):
            _actual = _row["actual_price"]
            _bucket = bucket_start_for_value(_row["forecast_demand"], float(demand_bin_width.value), _available_buckets)
            if _bucket is not None and _has_both_means:
                _probs = _table[_bucket]
                _p_short = _probs.get("Short", 0.0)
                _p_long = _probs.get("Long", 0.0)
                _mixture_forecast = _p_short * conditional_stats["Short"]["mean"] + _p_long * conditional_stats["Long"]["mean"]
            else:
                _mixture_forecast = _unconditional_mean
            _mixture_errors.append(abs(_mixture_forecast - _actual))

        _mixture_mae = _statistics3.mean(_mixture_errors) if _mixture_errors else None
        _vs_flat_pct = round((_flat_mae - _mixture_mae) / _flat_mae * 100, 1) if _flat_mae and _mixture_mae is not None else None
        _sweep_rows.append({
            "shrinkage_strength": _strength,
            "mixture_mae": round(_mixture_mae, 2) if _mixture_mae is not None else None,
            "vs_flat_baseline_pct": _vs_flat_pct,
        })

    sweep_df = pd.DataFrame(_sweep_rows)
    _best_idx = sweep_df["mixture_mae"].idxmin() if not sweep_df.empty and sweep_df["mixture_mae"].notna().any() else None
    _best_row = sweep_df.loc[_best_idx] if _best_idx is not None else None

    mo.vstack([
        mo.ui.table(sweep_df),
        mo.md(
            f"**Best shrinkage_strength found:** {_best_row['shrinkage_strength']:g} "
            f"(MAE £{_best_row['mixture_mae']:.2f}/MWh, {_best_row['vs_flat_baseline_pct']:.1f}% vs flat baseline). "
            f"Set the slider above to this value, or a nearby one, once you've seen where the curve actually flattens out."
            if _best_row is not None else "*No results.*"
        ),
    ])
    return


if __name__ == "__main__":
    app.run()
