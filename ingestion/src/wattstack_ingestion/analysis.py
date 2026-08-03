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
    substring match against each value in `battery_labels`)."""
    labels_lower = {label.lower() for label in battery_labels}
    result = set()
    for row in bmunits:
        value = str(row.get(label_field, "")).lower()
        if any(label in value for label in labels_lower):
            bmu_id = row.get(id_field)
            if bmu_id:
                result.add(bmu_id)
    return result


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
