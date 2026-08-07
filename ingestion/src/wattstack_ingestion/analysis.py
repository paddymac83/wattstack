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
