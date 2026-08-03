from wattstack_ingestion.analysis import (
    filter_battery_bmu_ids,
    marginal_bid_share,
    price_lookup_by_bmu_period,
)

BMUNITS = [
    {"bmuId": "T_BATT-1", "fuelType": "Battery"},
    {"bmuId": "T_BATT-2", "fuelType": "BATTERY STORAGE"},
    {"bmuId": "T_CCGT-1", "fuelType": "CCGT"},
    {"bmuId": "T_WIND-1", "fuelType": "Wind"},
]


def test_filter_battery_bmu_ids_matches_case_insensitively():
    ids = filter_battery_bmu_ids(BMUNITS, id_field="bmuId", label_field="fuelType", battery_labels={"battery"})
    assert ids == {"T_BATT-1", "T_BATT-2"}


def test_filter_battery_bmu_ids_excludes_non_matching():
    ids = filter_battery_bmu_ids(BMUNITS, id_field="bmuId", label_field="fuelType", battery_labels={"battery"})
    assert "T_CCGT-1" not in ids
    assert "T_WIND-1" not in ids


def test_filter_battery_bmu_ids_supports_multiple_labels():
    ids = filter_battery_bmu_ids(BMUNITS, id_field="bmuId", label_field="fuelType", battery_labels={"battery", "wind"})
    assert ids == {"T_BATT-1", "T_BATT-2", "T_WIND-1"}


# --- price_lookup_by_bmu_period: built from BOD (submitted prices), not BOALF ---

BID_OFFER_ROWS = [
    {"bmUnit": "T_BATT-1", "settlementPeriod": 1, "offer": 150.0},
    {"bmUnit": "T_BATT-1", "settlementPeriod": 1, "offer": 90.0},   # a second, less extreme tranche
    {"bmUnit": "T_CCGT-1", "settlementPeriod": 1, "offer": 90.0},
    {"bmUnit": "T_CCGT-1", "settlementPeriod": 2, "offer": -60.0},
    {"bmUnit": "T_WIND-1", "settlementPeriod": 2, "offer": 10.0},
    {"bmUnit": "T_BATT-2", "settlementPeriod": 3, "offer": 200.0},
]


def test_price_lookup_takes_most_extreme_price_per_bmu_period():
    lookup = price_lookup_by_bmu_period(
        BID_OFFER_ROWS, bmu_id_field="bmUnit", period_field="settlementPeriod", price_field="offer"
    )
    # T_BATT-1 submitted 150.0 and 90.0 in period 1 -- most extreme wins
    assert lookup[("T_BATT-1", 1)] == 150.0


def test_price_lookup_skips_rows_with_missing_price():
    rows = BID_OFFER_ROWS + [{"bmUnit": "T_BATT-1", "settlementPeriod": 5, "offer": None}]
    lookup = price_lookup_by_bmu_period(
        rows, bmu_id_field="bmUnit", period_field="settlementPeriod", price_field="offer"
    )
    assert ("T_BATT-1", 5) not in lookup


# --- marginal_bid_share: acceptances (who/when/how much) joined against price_lookup ---

ACCEPTANCES = [
    {"settlementPeriod": 1, "bmUnit": "T_BATT-1", "levelTo": 50},
    {"settlementPeriod": 1, "bmUnit": "T_CCGT-1", "levelTo": 30},
    {"settlementPeriod": 2, "bmUnit": "T_CCGT-1", "levelTo": 20},
    {"settlementPeriod": 2, "bmUnit": "T_WIND-1", "levelTo": 10},
    {"settlementPeriod": 3, "bmUnit": "T_BATT-2", "levelTo": 40},
    {"settlementPeriod": 4, "bmUnit": "T_CCGT-1", "levelTo": 5},  # no price known for this bmu/period
]


def _price_lookup():
    return price_lookup_by_bmu_period(
        BID_OFFER_ROWS, bmu_id_field="bmUnit", period_field="settlementPeriod", price_field="offer"
    )


def test_marginal_bid_share_identifies_battery_via_joined_price():
    result = marginal_bid_share(
        ACCEPTANCES, _price_lookup(), {"T_BATT-1", "T_BATT-2"},
        period_field="settlementPeriod", bmu_id_field="bmUnit",
    )
    assert result["periods"][1] is True    # battery's 150 beats CCGT's 90
    assert result["periods"][2] is False   # CCGT's -60 beats wind's 10
    assert result["periods"][3] is True    # only a battery accepted, and priced


def test_marginal_bid_share_excludes_periods_with_no_known_price():
    result = marginal_bid_share(
        ACCEPTANCES, _price_lookup(), {"T_BATT-1", "T_BATT-2"},
        period_field="settlementPeriod", bmu_id_field="bmUnit",
    )
    assert 4 not in result["periods"]  # accepted, but no BOD price known for that bmu/period
    assert result["n_periods"] == 3


def test_marginal_bid_share_computes_overall_share():
    result = marginal_bid_share(
        ACCEPTANCES, _price_lookup(), {"T_BATT-1", "T_BATT-2"},
        period_field="settlementPeriod", bmu_id_field="bmUnit",
    )
    assert abs(result["share"] - 2 / 3) < 1e-9


def test_marginal_bid_share_handles_no_battery_bmus():
    result = marginal_bid_share(
        ACCEPTANCES, _price_lookup(), set(),
        period_field="settlementPeriod", bmu_id_field="bmUnit",
    )
    assert result["share"] == 0.0


def test_marginal_bid_share_handles_empty_acceptances():
    result = marginal_bid_share(
        [], {}, {"T_BATT-1"}, period_field="settlementPeriod", bmu_id_field="bmUnit",
    )
    assert result["n_periods"] == 0
    assert result["share"] == 0.0


def test_marginal_bid_share_handles_empty_price_lookup():
    result = marginal_bid_share(
        ACCEPTANCES, {}, {"T_BATT-1", "T_BATT-2"},
        period_field="settlementPeriod", bmu_id_field="bmUnit",
    )
    assert result["n_periods"] == 0  # every period excluded -- no prices known at all
