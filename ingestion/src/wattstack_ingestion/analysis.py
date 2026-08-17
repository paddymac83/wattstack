"""Reusable, testable analysis functions -- the logic behind the
interactive notebook, kept separate from it on purpose.

Everything here takes field names as parameters rather than assuming
them. That's not extra ceremony -- it's the honest response to not
knowing Elexon's real schemas yet (see elexon.py). The marimo
notebook is where you discover the real field names and pass them in;
these functions are what you'd promote into `core` (properly typed,
real field names hardcoded) once you've settled on a definition worth
shipping.

marginal_bid_share() went through a real correction, not a minor
tweak -- see ADR 0006. The first version assumed accepted bid/offer
actions (BOALF) carried a price directly. They don't: BOALF is
volume and timing only (which BM unit, how much, when). Price comes
from a separate dataset, Bid-Offer Data (BOD) -- what each unit
submitted, not what got accepted. Getting a price for a specific
acceptance means joining the two on BM unit and settlement period,
and that join is itself an approximation: BOD is the full price/
volume ladder a unit submitted, not a record of which specific rung
of it a given acceptance used. price_lookup_by_bmu_period() takes the
most extreme price a unit submitted in a period as a stand-in for
"the price of what got accepted" -- a second layer of approximation
on top of the first (most extreme accepted price = "marginal"),
worth remembering when reading the resulting numbers as more precise
than they are.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict


def filter_battery_bmu_ids(
    bmunits: list[dict],
    id_field: str,
    label_field: str,
    battery_labels: set[str],
) -> set[str]:
    """Which BM unit IDs count as batteries, per whatever label your
    chosen `label_field` uses (e.g. a fuel-type or technology column)
    and whatever labels you consider a battery (case-insensitive
    substring match against each value in `battery_labels`).

    Known real limitation, confirmed against live data: Elexon's own
    `fuelType` field on /reference/bmunits/all does not reliably tag
    BESS as its own category -- this function will under-match (miss
    real batteries entirely) if `label_field` is `fuelType` and no
    value there ever says anything battery-like. See
    filter_bmus_by_id_pattern() for a second, independent signal
    worth combining with this one (union the two result sets) rather
    than relying on fuel-type text alone.
    """
    labels_lower = {label.lower() for label in battery_labels}
    result = set()
    for row in bmunits:
        value = str(row.get(label_field, "")).lower()
        if any(label in value for label in labels_lower):
            bmu_id = row.get(id_field)
            if bmu_id:
                result.add(bmu_id)
    return result


def filter_bmus_by_id_pattern(bmunits: list[dict], id_field: str, pattern: str) -> set[str]:
    """Which BM unit IDs match a regex against the ID itself, e.g.
    identifying BESS units by an ID-naming convention when fuelType
    doesn't reliably tag them (a real, confirmed gap -- see
    filter_battery_bmu_ids()'s docstring).

    Deliberately NOT given a hardcoded default pattern. A plausible-
    looking convention like "ends in B-<number>" genuinely matches
    several real, well-known GB power stations that are not
    batteries -- Aberthaw B, Dungeness B, Hinkley Point B, Ironbridge
    B, Rugeley B, and Tilbury B are all real BM units whose names,
    and very plausibly whose IDs, end that way for reasons that have
    nothing to do with storage (a second generating unit at the same
    site). Any pattern used here needs its actual matches checked by
    eye -- see the notebook's match-list display -- not trusted
    because it looks reasonable.
    """
    regex = re.compile(pattern)
    return {row[id_field] for row in bmunits if row.get(id_field) and regex.search(row[id_field])}


def price_lookup_by_bmu_period(
    bid_offer_rows: list[dict],
    bmu_id_field: str,
    period_field: str,
    price_field: str,
) -> dict[tuple, float]:
    """From raw Bid-Offer Data (BOD) rows, build {(bmu_id, period):
    price} -- the most extreme price that unit submitted in that
    period. A proxy for "the price of what got accepted," not a
    record of it -- see module docstring for why that's a real
    approximation, not just caution.
    """
    lookup: dict[tuple, float] = {}
    for row in bid_offer_rows:
        bmu = row.get(bmu_id_field)
        period = row.get(period_field)
        price = row.get(price_field)
        if bmu is None or period is None or price is None:
            continue
        key = (bmu, period)
        if key not in lookup or abs(price) > abs(lookup[key]):
            lookup[key] = price
    return lookup


def marginal_bid_share(
    acceptances: list[dict],
    price_lookup: dict[tuple, float],
    battery_bmu_ids: set[str],
    period_field: str,
    bmu_id_field: str,
) -> dict:
    """For each settlement period, among the BM units accepted
    (BOALF) whose price is known (via price_lookup, built from BOD --
    see price_lookup_by_bmu_period), find whichever has the most
    extreme price and check whether it's a battery.

    Periods where no accepted unit has a known price are excluded
    from n_periods entirely, not counted as False -- an unknown price
    is not the same claim as "not a battery."

    Returns {"periods": {period: bool}, "share": float, "n_periods": int}.
    """
    accepted_bmus_by_period: dict = defaultdict(set)
    for row in acceptances:
        period = row.get(period_field)
        bmu = row.get(bmu_id_field)
        if period is not None and bmu is not None:
            accepted_bmus_by_period[period].add(bmu)

    periods_result: dict = {}
    for period, bmus in accepted_bmus_by_period.items():
        priced = [(bmu, price_lookup[(bmu, period)]) for bmu in bmus if (bmu, period) in price_lookup]
        if not priced:
            continue
        most_extreme_bmu = max(priced, key=lambda pair: abs(pair[1]))[0]
        periods_result[period] = most_extreme_bmu in battery_bmu_ids

    n_periods = len(periods_result)
    share = sum(periods_result.values()) / n_periods if n_periods else 0.0
    return {"periods": periods_result, "share": share, "n_periods": n_periods}


def classify_system_length(niv_mwh: float) -> str:
    """Long vs short, per Elexon's own SPAR methodology page: short
    NIVs are positive, long NIVs are negative --
    https://www.elexon.co.uk/bsc/data/system-prices-analysis-report/
    (confirmed directly from source, not inferred from general BM
    terminology).

    NIV of exactly zero is classified Short here -- a tie-break, not
    a documented convention; the source material describes exactly-
    zero NIV as vanishingly rare in practice (its PAR Tagging section
    notes almost no settlement periods are unaffected by rounding at
    that boundary), so this rarely matters in real data.
    """
    return "Short" if niv_mwh >= 0 else "Long"


def bin_counts_by_group(values: list[float], groups: list[str], bin_width: float) -> dict:
    """Bin `values` into bin_width-wide buckets and count occurrences
    per (bin, group) -- e.g. settlement-period counts per GBP20/MWh
    system-price bin, split by Long/Short.

    Bin edges are computed from the actual data's min/max, rounded
    outward to clean multiples of bin_width, so bins stay a fixed
    width regardless of which month's data this runs against.

    Returns {"bin_labels": [...], "groups": [...], "counts": {group:
    [count_per_bin, ...]}} -- shaped directly for a grouped bar chart,
    every group's counts aligned to the same bin_labels order.
    """
    if not values:
        return {"bin_labels": [], "groups": sorted(set(groups)), "counts": {}}

    lo = math.floor(min(values) / bin_width) * bin_width
    hi = math.ceil(max(values) / bin_width) * bin_width
    n_bins = max(int(round((hi - lo) / bin_width)), 1)

    bin_labels = [f"{lo + i * bin_width:g} to {lo + (i + 1) * bin_width:g}" for i in range(n_bins)]
    unique_groups = sorted(set(groups))
    counts = {g: [0] * n_bins for g in unique_groups}

    for value, group in zip(values, groups):
        idx = int((value - lo) // bin_width)
        idx = min(max(idx, 0), n_bins - 1)  # clamp a value exactly at hi into the last bin, not a phantom extra one
        counts[group][idx] += 1

    return {"bin_labels": bin_labels, "groups": unique_groups, "counts": counts}


def offer_volume(level_from: float, level_to: float) -> float:
    """Proxy for the volume of an accepted OFFER (a balancing action
    to increase output / decrease demand): the positive part of the
    level change within one acceptance.

    NOT a precise, settlement-grade MWh figure -- that needs the
    acceptance's actual delivery duration and possibly ramp profile
    within the settlement period, neither of which this uses. A
    relative-magnitude proxy for comparing which fuel types
    contributed most, in the same spirit as marginal_bid_share()'s
    approximations -- see ADR for this notebook. Negative deltas
    (bid-direction changes) return 0.0; use bid_volume() for those.
    """
    return max(level_to - level_from, 0.0)


def bid_volume(level_from: float, level_to: float) -> float:
    """Symmetric to offer_volume() -- the positive part of a DEcrease
    in level, i.e. an accepted Bid. Same proxy caveats apply."""
    return max(level_from - level_to, 0.0)


def fuel_type_lookup(bmunits: list[dict], id_field: str, fuel_type_field: str) -> dict:
    """BM unit ID -> fuel type, from reference data. Generalises
    filter_battery_bmu_ids() into a full categorical map rather than
    a single battery/not-battery split -- for charts that need every
    unit's category, not just whether it's a battery."""
    return {row[id_field]: row.get(fuel_type_field, "Unknown") for row in bmunits if row.get(id_field)}


def aggregate_volume_by_day_and_category(
    rows: list[dict], date_field: str, category_field: str, volume_field: str
) -> dict:
    """Sum `volume_field` grouped by (date, category). Generic on
    purpose: the same shape works for accepted offer volume, accepted
    bid volume, or anything else that's one row per action with a
    date/category/volume already attached by the caller.

    Returns {"dates": [sorted...], "categories": [sorted...],
    "volumes": {category: [volume_per_date, aligned to "dates"]}} --
    shaped directly for a stacked bar chart.
    """
    totals: dict = defaultdict(float)
    dates = set()
    categories = set()
    for row in rows:
        d = row.get(date_field)
        c = row.get(category_field)
        v = row.get(volume_field) or 0.0
        if d is None or c is None:
            continue
        dates.add(d)
        categories.add(c)
        totals[(d, c)] += v

    sorted_dates = sorted(dates)
    sorted_categories = sorted(categories)
    volumes = {c: [totals.get((d, c), 0.0) for d in sorted_dates] for c in sorted_categories}
    return {"dates": sorted_dates, "categories": sorted_categories, "volumes": volumes}


def is_flagged(value: object) -> bool:
    """Normalise a flag field's raw value to a real boolean.

    BOALF's exact encoding for SO-Flag (true JSON boolean? "Y"/"N"
    string? 1/0?) was not confirmed live -- the dataset's full name,
    "Bid Offer Acceptance Level Flagged," is strong evidence a flag
    field exists there, but not what it looks like on the wire. This
    handles the common encodings defensively rather than assuming
    one; run the notebook's schema preview to see the real value
    shape before trusting this blindly.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "y", "yes", "1"}


def spread_by_bin(bin_values: list[float], target_values: list[float], bin_width: float) -> dict:
    """Bin `bin_values` into bin_width-wide buckets and compute the
    spread (standard deviation) of `target_values` within each bucket
    -- e.g. does a higher wind forecast bucket correlate with more
    volatile (higher std-dev) actual System Prices, regardless of
    which direction they move.

    Deliberately different from bin_counts_by_group(): that answers
    "does this variable predict direction" (counts per group); this
    answers "does this variable predict dispersion" (spread within a
    bucket) -- volatility and direction are different questions, and
    conflating them by reusing the counting function would answer the
    wrong one.

    Returns {"bin_labels": [...], "counts": [...], "means": [...],
    "std_devs": [...]} -- aligned lists, one entry per bin. A bucket
    with fewer than 2 observations gets std_dev 0.0 (undefined
    otherwise), not an error -- worth checking `counts` before
    trusting a bucket's std_dev if the sample is thin.
    """
    if not bin_values:
        return {"bin_labels": [], "counts": [], "means": [], "std_devs": []}

    lo = math.floor(min(bin_values) / bin_width) * bin_width
    hi = math.ceil(max(bin_values) / bin_width) * bin_width
    n_bins = max(int(round((hi - lo) / bin_width)), 1)

    bin_labels = [f"{lo + i * bin_width:g} to {lo + (i + 1) * bin_width:g}" for i in range(n_bins)]
    buckets: list[list[float]] = [[] for _ in range(n_bins)]

    for bv, tv in zip(bin_values, target_values):
        idx = int((bv - lo) // bin_width)
        idx = min(max(idx, 0), n_bins - 1)  # clamp a value exactly at hi into the last bin
        buckets[idx].append(tv)

    counts = [len(b) for b in buckets]
    means = [round(statistics.mean(b), 2) if b else 0.0 for b in buckets]
    std_devs = [round(statistics.stdev(b), 2) if len(b) >= 2 else 0.0 for b in buckets]

    return {"bin_labels": bin_labels, "counts": counts, "means": means, "std_devs": std_devs}


# GB's 6 standard EFA blocks, confirmed via a real NESO Frequency
# Response Market Information Report (April 2023): "Procurement of
# SFFR takes place across the standard 6 EFA blocks. Bids must
# therefore only start, and end, at the following times: 2300, 0300,
# 0700, 1100, 1500 and 1900." Each block is 4 hours; the EFA day
# itself starts at 23:00, not midnight -- period 1 of an EFA day is
# the 23:00 block of the *previous* calendar day.
EFA_BLOCKS = [
    (23, 3), (3, 7), (7, 11), (11, 15), (15, 19), (19, 23),
]


def efa_block_label(hour: int) -> str:
    """Which of GB's 6 standard EFA blocks a given hour (0-23) falls
    in. Returns a label like "23:00-03:00" -- deliberately a string,
    not just an index, so it's directly usable as a chart category
    without a separate lookup table.

    Only takes the hour, not settlement period or minute, since every
    EFA block boundary in EFA_BLOCKS falls on a whole hour -- correct
    for GB's actual EFA block definition, not a simplification.
    """
    if not (0 <= hour <= 23):
        raise ValueError(f"hour must be 0-23, got {hour}")

    for start_hour, end_hour in EFA_BLOCKS:
        if start_hour < end_hour:
            if start_hour <= hour < end_hour:
                return f"{start_hour:02d}:00-{end_hour:02d}:00"
        else:  # wraps past midnight (the 23:00-03:00 block)
            if hour >= start_hour or hour < end_hour:
                return f"{start_hour:02d}:00-{end_hour:02d}:00"
    raise ValueError(f"hour must be 0-23, got {hour}")  # pragma: no cover -- unreachable, blocks cover all 24 hours


def efa_block_label_for_index(efa_number: int) -> str:
    """Map a 1-indexed EFA block number to the same label format
    efa_block_label() produces from an hour -- for datasets that give
    the block directly as a number (or a column name like "EFA1"),
    rather than a timestamp to derive it from. Confirmed live: NESO's
    own DC requirements CSV has exactly this shape (columns
    EFA1..EFA6, one row per Service_Type/forecast).

    Assumes NESO numbers blocks sequentially starting from the 23:00
    block as EFA1 -- the standard GB convention (matches EFA_BLOCKS'
    own order), but not independently confirmed for this specific
    column-naming scheme. Worth a sanity check against known
    operational patterns (e.g. higher evening-peak requirement)
    before trusting the mapping blindly on a new dataset.
    """
    if not (1 <= efa_number <= 6):
        raise ValueError(f"efa_number must be 1-6, got {efa_number}")
    start_hour, end_hour = EFA_BLOCKS[efa_number - 1]
    return f"{start_hour:02d}:00-{end_hour:02d}:00"


def efa_block_number_for_hour(hour: int) -> int:
    """Which of GB's 6 standard EFA blocks (1-6) a given hour (0-23)
    falls in -- the inverse of efa_block_label_for_index(). Needed
    when a dataset gives a delivery timestamp rather than an EFA block
    number or label directly (e.g. DC auction results, confirmed to
    use deliveryStart/deliveryEnd timestamps rather than the
    EFA1..EFA6 wide-column shape the DC requirements CSV used).

    Same confirmed block boundaries as efa_block_label()/EFA_BLOCKS --
    this just returns the 1-6 number instead of the "HH:MM-HH:MM"
    label, for callers that want to bucket/aggregate by block number
    directly (e.g. as the period argument to seasonal_average_by_period()).
    """
    if not (0 <= hour <= 23):
        raise ValueError(f"hour must be 0-23, got {hour}")
    for i, (start_hour, end_hour) in enumerate(EFA_BLOCKS, start=1):
        if start_hour < end_hour:
            if start_hour <= hour < end_hour:
                return i
        else:  # wraps past midnight (the 23:00-03:00 block)
            if hour >= start_hour or hour < end_hour:
                return i
    raise ValueError(f"hour must be 0-23, got {hour}")  # pragma: no cover -- unreachable, blocks cover all 24 hours


def direction_from_sign(value: float) -> str:
    """Classify a signed flow value as "Import" (positive) or
    "Export" (negative) -- the sign convention believed to apply to
    interconnector rows in Elexon's B1610 data (generation/import
    positive, demand/export negative), matching how every other BMU's
    generation is signed. NOT independently confirmed for
    interconnectors specifically -- check it against a real response
    (a known large importer or exporter's sign should be checkable
    against known real-world flow direction on a given day) before
    trusting it.

    Zero is classified Import -- a tie-break for a case that's
    vanishingly rare in real flow data, not a documented convention.
    """
    return "Import" if value >= 0 else "Export"


def largest_value_by_group(rows: list[dict], group_field: str, value_field: str) -> dict:
    """For each distinct value of group_field, find the maximum value
    of value_field among rows in that group -- e.g. per day, the
    single largest generation/import output among a candidate set of
    BM units, a proxy for the "largest secured loss" NESO would need
    to be ready for if that unit or interconnector tripped.

    Takes values as given -- does NOT take an absolute value itself,
    so pre-filter/sign rows the way the specific question needs first
    (e.g. filter to only Export-direction rows via direction_from_sign()
    and pass abs() values in, if what's wanted is the largest export
    magnitude specifically).

    Returns {group_value: {"max_value": float, "count": int}} -- the
    count is how many candidate rows contributed to that group, worth
    checking the same way spread_by_bin()'s counts are: a "largest of
    1" is a very different claim from a "largest of 40".
    """
    groups = defaultdict(list)
    for row in rows:
        key = row.get(group_field)
        value = row.get(value_field)
        if key is None or value is None:
            continue
        groups[key].append(float(value))

    return {key: {"max_value": max(values), "count": len(values)} for key, values in groups.items()}


def seasonal_average_by_period(
    rows: list[dict], period_field: str, value_field: str, exclude_values: set = frozenset({0.0})
) -> dict[int, float]:
    """Average value per settlement period (1-48), pooled across
    every day in `rows` -- a "climatological"/seasonal-average
    baseline, not a forecast. Built for exactly one situation: a price
    signal that only exists as historical/realised data (e.g. Market
    Index Data), needed before it exists as a live fact for the day
    being planned -- e.g. optimizing before N2EX's day-ahead gate
    closure (09:50), when tomorrow's wholesale price genuinely isn't
    settled yet.

    `exclude_values` defaults to excluding exact zero -- a real,
    sourced data-quality issue with MID specifically (N2EX shows
    all-zero values for some dates) that would otherwise silently
    drag the average toward zero rather than reflecting a real price.
    Pass an empty set to disable exclusion if that's not appropriate
    for a different data source.

    Deliberately does not split by day-of-week or season -- pooling
    every available day is the simple v1 version; a real predictive
    model (using demand/wind forecasts as explanatory inputs) or a
    day-of-week-aware average are both real future refinements, not
    built here.

    Returns {period: average_value} -- periods with no surviving
    observations (all excluded or never seen) are simply absent, not
    zero-filled; the caller decides how to handle a missing period.
    """
    buckets: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        period = row.get(period_field)
        value = row.get(value_field)
        if period is None or value is None:
            continue
        value = float(value)
        if value in exclude_values:
            continue
        buckets[int(period)].append(value)

    return {period: statistics.mean(values) for period, values in buckets.items()}


def probability_by_bin(bin_values: list[float], group_values: list[str], bin_width: float) -> dict:
    """Empirical P(group | value falls in this bucket) -- e.g. P(Short
    | forecast demand in [28000, 29000)) -- estimated from real
    historical (bin_value, group) pairs. Built for exactly one
    situation: converting a forecast input (demand, wind, etc.) into a
    genuine probability of system length, the way Browell & Gilbert
    (Energies 2022) describe for day-ahead imbalance price forecasting
    -- not a classification, a probability, since the downstream use
    is a probability-weighted mixture of conditional price
    distributions.

    Reuses bin_counts_by_group()'s own bucket definition (same
    flooring/rounding), so the two stay consistent by construction --
    this isn't a reimplementation of the bucketing logic, just a
    normalisation of its output into probabilities.

    Returns {bin_start: {group: probability}} -- keyed by each
    bucket's NUMERIC start (not a display label), specifically so a
    new value can be looked up via bucket_start_for_value() without
    re-deriving bucket boundaries by hand. A bucket with zero observed
    rows is omitted entirely, not returned with undefined
    probabilities.
    """
    if not bin_values:
        return {}

    counts = bin_counts_by_group(bin_values, group_values, bin_width)
    lo = math.floor(min(bin_values) / bin_width) * bin_width

    result = {}
    for i in range(len(counts["bin_labels"])):
        bin_start = lo + i * bin_width
        total = sum(counts["counts"][g][i] for g in counts["groups"])
        if total == 0:
            continue
        result[bin_start] = {g: counts["counts"][g][i] / total for g in counts["groups"]}
    return result


def shrink_probability_by_bin(
    bin_values: list[float], group_values: list[str], bin_width: float, shrinkage_strength: float = 10.0
) -> dict:
    """Empirical P(group | bucket), shrunk toward the overall
    (unconditional) rate for buckets with few observations -- a
    direct, principled fix for a real failure mode found in practice,
    not a hypothetical one: probability_by_bin()'s raw empirical
    frequency can be extremely noisy for a thin bucket (2
    observations happening to both be "Short" gives 100%, purely by
    chance), and a mixture model built on an overconfident, noisy
    probability can be WORSE than not using the probability at all --
    confidently wrong costs more than an honest average when the
    underlying classifier is unreliable. This is exactly what
    happened when demand-bucket probability was used raw in
    notebooks/imbalance_price_probabilistic_forecast.py: a mixture
    MAE dramatically worse than the flat baseline.

    `shrinkage_strength` (k) is a pseudo-count: a bucket's estimate is
    pulled toward the unconditional rate as if k additional
    observations at that rate had already been seen. A bucket with
    many real observations is barely affected (its own data
    dominates); a bucket with few is pulled strongly toward the
    population average. k=0 recovers probability_by_bin()'s raw
    frequency exactly. The right value for k is itself an empirical
    question -- this is a starting point to tune against a real
    backtest, not a calibrated final answer.

    Returns {bin_start: {group: probability}}, the identical shape to
    probability_by_bin() -- a drop-in replacement, not a different
    interface to learn.
    """
    if not bin_values:
        return {}

    counts = bin_counts_by_group(bin_values, group_values, bin_width)
    lo = math.floor(min(bin_values) / bin_width) * bin_width

    overall_totals = {g: sum(counts["counts"][g]) for g in counts["groups"]}
    overall_total = sum(overall_totals.values())
    unconditional_rate = {
        g: (overall_totals[g] / overall_total if overall_total else 0.0) for g in counts["groups"]
    }

    result = {}
    for i in range(len(counts["bin_labels"])):
        bin_start = lo + i * bin_width
        bucket_counts = {g: counts["counts"][g][i] for g in counts["groups"]}
        bucket_total = sum(bucket_counts.values())
        if bucket_total == 0:
            continue
        result[bin_start] = {
            g: (bucket_counts[g] + shrinkage_strength * unconditional_rate[g]) / (bucket_total + shrinkage_strength)
            for g in counts["groups"]
        }
    return result


def bucket_start_for_value(value: float, bin_width: float, available_bucket_starts) -> float | None:
    """Which trained bucket (matching probability_by_bin()'s keys) a
    new value falls into -- e.g. tomorrow's forecast demand, looked up
    against a probability table built from historical data.

    Falls back to the NEAREST available bucket (by numeric distance)
    if the value's own raw bucket wasn't present in training data --
    tomorrow's forecast can genuinely fall outside the range a
    training window happened to cover; returning nothing useful in
    that case would silently break the downstream forecast rather
    than degrading gracefully to the closest evidence available.

    Returns None only if available_bucket_starts is empty -- there's
    nothing to fall back to.
    """
    available_bucket_starts = list(available_bucket_starts)
    if not available_bucket_starts:
        return None
    raw_start = math.floor(value / bin_width) * bin_width
    if raw_start in available_bucket_starts:
        return raw_start
    return min(available_bucket_starts, key=lambda b: abs(b - raw_start))
