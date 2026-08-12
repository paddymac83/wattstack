import pytest

from wattstack_ingestion.analysis import (
    aggregate_volume_by_day_and_category,
    bid_volume,
    bin_counts_by_group,
    classify_system_length,
    direction_from_sign,
    efa_block_label,
    efa_block_label_for_index,
    efa_block_number_for_hour,
    filter_battery_bmu_ids,
    filter_bmus_by_id_pattern,
    fuel_type_lookup,
    is_flagged,
    largest_value_by_group,
    marginal_bid_share,
    offer_volume,
    price_lookup_by_bmu_period,
    seasonal_average_by_period,
    spread_by_bin,
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


# --- filter_bmus_by_id_pattern ---


def test_filter_bmus_by_id_pattern_matches_regex_against_id():
    bmunits = [{"bmuId": "T_KILSB-2"}, {"bmuId": "T_WIND-1"}]
    matched = filter_bmus_by_id_pattern(bmunits, id_field="bmuId", pattern=r"B-\d+$")
    assert matched == {"T_KILSB-2"}


def test_filter_bmus_by_id_pattern_demonstrates_the_real_false_positive_risk():
    """A plausible-looking 'ends in B-<number>' pattern also matches
    real, well-known non-battery GB power stations whose names end
    in B for unrelated reasons -- exactly why this function takes an
    arbitrary pattern rather than shipping a trusted default."""
    bmunits = [{"bmuId": "T_KILSB-2"}, {"bmuId": "T_DUNGB-1"}, {"bmuId": "T_HPB-3"}]
    matched = filter_bmus_by_id_pattern(bmunits, id_field="bmuId", pattern=r"B-\d+$")
    # all three match the naive pattern -- only the first is actually a battery
    assert matched == {"T_KILSB-2", "T_DUNGB-1", "T_HPB-3"}


def test_filter_bmus_by_id_pattern_skips_rows_without_an_id():
    bmunits = [{"notAnId": "x"}]
    matched = filter_bmus_by_id_pattern(bmunits, id_field="bmuId", pattern=r".*")
    assert matched == set()


def test_filter_bmus_by_id_pattern_returns_empty_set_when_nothing_matches():
    bmunits = [{"bmuId": "T_WIND-1"}]
    matched = filter_bmus_by_id_pattern(bmunits, id_field="bmuId", pattern=r"ZZZ")
    assert matched == set()


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


# --- classify_system_length ---


def test_classify_system_length_positive_niv_is_short():
    assert classify_system_length(150.0) == "Short"


def test_classify_system_length_negative_niv_is_long():
    assert classify_system_length(-150.0) == "Long"


def test_classify_system_length_zero_niv_is_short_by_tiebreak():
    assert classify_system_length(0.0) == "Short"


# --- bin_counts_by_group ---


def test_bin_counts_by_group_bins_and_counts_correctly():
    values = [5.0, 15.0, 25.0, 35.0]  # bins of width 20: [0,20), [20,40)
    groups = ["Short", "Short", "Long", "Long"]
    result = bin_counts_by_group(values, groups, bin_width=20.0)
    assert result["bin_labels"] == ["0 to 20", "20 to 40"]
    assert result["counts"]["Short"] == [2, 0]
    assert result["counts"]["Long"] == [0, 2]


def test_bin_counts_by_group_handles_negative_values():
    values = [-15.0, 5.0]
    groups = ["Long", "Short"]
    result = bin_counts_by_group(values, groups, bin_width=20.0)
    assert result["bin_labels"] == ["-20 to 0", "0 to 20"]
    assert result["counts"]["Long"] == [1, 0]
    assert result["counts"]["Short"] == [0, 1]


def test_bin_counts_by_group_clamps_value_exactly_at_max_into_last_bin():
    values = [0.0, 20.0]  # 20.0 is exactly the upper edge
    groups = ["Short", "Short"]
    result = bin_counts_by_group(values, groups, bin_width=20.0)
    assert result["bin_labels"] == ["0 to 20"]
    assert result["counts"]["Short"] == [2]


def test_bin_counts_by_group_handles_empty_input():
    result = bin_counts_by_group([], [], bin_width=20.0)
    assert result["bin_labels"] == []
    assert result["counts"] == {}


# --- offer_volume / bid_volume ---


def test_offer_volume_is_positive_delta():
    assert offer_volume(level_from=100.0, level_to=150.0) == 50.0


def test_offer_volume_is_zero_for_a_decrease():
    assert offer_volume(level_from=150.0, level_to=100.0) == 0.0


def test_bid_volume_is_positive_for_a_decrease():
    assert bid_volume(level_from=150.0, level_to=100.0) == 50.0


def test_bid_volume_is_zero_for_an_increase():
    assert bid_volume(level_from=100.0, level_to=150.0) == 0.0


# --- fuel_type_lookup ---


def test_fuel_type_lookup_maps_every_unit():
    bmunits = [{"bmuId": "T_GAS-1", "fuelType": "CCGT"}, {"bmuId": "T_WIND-1", "fuelType": "WIND"}]
    lookup = fuel_type_lookup(bmunits, id_field="bmuId", fuel_type_field="fuelType")
    assert lookup == {"T_GAS-1": "CCGT", "T_WIND-1": "WIND"}


def test_fuel_type_lookup_defaults_missing_fuel_type_to_unknown():
    bmunits = [{"bmuId": "T_X-1"}]
    lookup = fuel_type_lookup(bmunits, id_field="bmuId", fuel_type_field="fuelType")
    assert lookup["T_X-1"] == "Unknown"


def test_fuel_type_lookup_skips_rows_without_an_id():
    bmunits = [{"fuelType": "WIND"}]
    lookup = fuel_type_lookup(bmunits, id_field="bmuId", fuel_type_field="fuelType")
    assert lookup == {}


# --- aggregate_volume_by_day_and_category ---


def test_aggregate_volume_sums_matching_day_and_category():
    rows = [
        {"day": "2026-06-01", "cat": "Gas", "vol": 10.0},
        {"day": "2026-06-01", "cat": "Gas", "vol": 5.0},
        {"day": "2026-06-01", "cat": "Wind", "vol": 3.0},
        {"day": "2026-06-02", "cat": "Gas", "vol": 7.0},
    ]
    result = aggregate_volume_by_day_and_category(rows, date_field="day", category_field="cat", volume_field="vol")
    assert result["dates"] == ["2026-06-01", "2026-06-02"]
    assert result["categories"] == ["Gas", "Wind"]
    assert result["volumes"]["Gas"] == [15.0, 7.0]
    assert result["volumes"]["Wind"] == [3.0, 0.0]  # zero-filled for the day it has no volume


def test_aggregate_volume_skips_rows_missing_day_or_category():
    rows = [{"day": None, "cat": "Gas", "vol": 10.0}, {"day": "2026-06-01", "cat": "Gas", "vol": 5.0}]
    result = aggregate_volume_by_day_and_category(rows, date_field="day", category_field="cat", volume_field="vol")
    assert result["dates"] == ["2026-06-01"]
    assert result["volumes"]["Gas"] == [5.0]


def test_aggregate_volume_treats_missing_volume_as_zero():
    rows = [{"day": "2026-06-01", "cat": "Gas"}]
    result = aggregate_volume_by_day_and_category(rows, date_field="day", category_field="cat", volume_field="vol")
    assert result["volumes"]["Gas"] == [0.0]


# --- is_flagged ---


def test_is_flagged_handles_real_booleans():
    assert is_flagged(True) is True
    assert is_flagged(False) is False


def test_is_flagged_handles_yes_no_strings_case_insensitively():
    assert is_flagged("Y") is True
    assert is_flagged("yes") is True
    assert is_flagged("N") is False
    assert is_flagged("no") is False


def test_is_flagged_handles_stringified_booleans():
    assert is_flagged("true") is True
    assert is_flagged("True") is True
    assert is_flagged("false") is False


def test_is_flagged_handles_numeric_encoding():
    assert is_flagged(1) is True
    assert is_flagged(0) is False


def test_is_flagged_treats_none_as_unflagged():
    assert is_flagged(None) is False


# --- spread_by_bin ---


def test_spread_by_bin_computes_mean_and_stdev_per_bucket():
    # bin 0-10: prices [40, 42] -> low spread. bin 10-20: prices [10, 90] -> high spread.
    bin_values = [5.0, 6.0, 15.0, 16.0]
    target_values = [40.0, 42.0, 10.0, 90.0]
    result = spread_by_bin(bin_values, target_values, bin_width=10.0)
    assert result["bin_labels"] == ["0 to 10", "10 to 20"]
    assert result["counts"] == [2, 2]
    assert result["means"] == [41.0, 50.0]
    assert result["std_devs"][0] < result["std_devs"][1]  # first bucket is genuinely less volatile


def test_spread_by_bin_gives_zero_stdev_for_single_observation_bucket():
    result = spread_by_bin([5.0], [40.0], bin_width=10.0)
    assert result["counts"] == [1]
    assert result["std_devs"] == [0.0]  # undefined for n=1, not an error


def test_spread_by_bin_handles_empty_input():
    result = spread_by_bin([], [], bin_width=10.0)
    assert result == {"bin_labels": [], "counts": [], "means": [], "std_devs": []}


def test_spread_by_bin_clamps_value_at_upper_edge_into_last_bin():
    result = spread_by_bin([0.0, 10.0], [1.0, 2.0], bin_width=10.0)
    assert result["bin_labels"] == ["0 to 10"]
    assert result["counts"] == [2]


# --- efa_block_label ---


def test_efa_block_label_for_each_block_start_hour():
    assert efa_block_label(23) == "23:00-03:00"
    assert efa_block_label(3) == "03:00-07:00"
    assert efa_block_label(7) == "07:00-11:00"
    assert efa_block_label(11) == "11:00-15:00"
    assert efa_block_label(15) == "15:00-19:00"
    assert efa_block_label(19) == "19:00-23:00"


def test_efa_block_label_for_hours_within_a_block_not_just_boundaries():
    assert efa_block_label(0) == "23:00-03:00"   # midnight, inside the wraparound block
    assert efa_block_label(1) == "23:00-03:00"
    assert efa_block_label(5) == "03:00-07:00"
    assert efa_block_label(22) == "19:00-23:00"


def test_efa_block_label_covers_all_24_hours_without_gaps_or_overlap():
    """Every hour must map to exactly one block -- confirms the
    wraparound logic and the boundary conditions are both correct,
    not just spot-checked at a few points."""
    labels = [efa_block_label(h) for h in range(24)]
    assert len(labels) == 24
    assert all(labels)  # none raised, none empty
    assert len(set(labels)) == 6  # exactly the 6 standard blocks, no extras


def test_efa_block_label_rejects_invalid_hour():
    with pytest.raises(ValueError):
        efa_block_label(24)


# --- efa_block_label_for_index ---


def test_efa_block_label_for_index_matches_efa_block_label_at_block_starts():
    """EFA1..EFA6 should map to exactly the same labels
    efa_block_label() produces from each block's start hour -- these
    two functions describe the same 6 blocks from different input
    shapes and must agree."""
    assert efa_block_label_for_index(1) == efa_block_label(23)
    assert efa_block_label_for_index(2) == efa_block_label(3)
    assert efa_block_label_for_index(3) == efa_block_label(7)
    assert efa_block_label_for_index(4) == efa_block_label(11)
    assert efa_block_label_for_index(5) == efa_block_label(15)
    assert efa_block_label_for_index(6) == efa_block_label(19)


def test_efa_block_label_for_index_rejects_out_of_range():
    with pytest.raises(ValueError):
        efa_block_label_for_index(0)
    with pytest.raises(ValueError):
        efa_block_label_for_index(7)


# --- efa_block_number_for_hour ---


def test_efa_block_number_for_hour_is_the_genuine_inverse_of_efa_block_label_for_index():
    """For every hour, the block number this returns should map back
    (via efa_block_label_for_index) to the same label
    efa_block_label() would give directly -- confirms these three
    functions agree with each other, not just individually correct."""
    for hour in range(24):
        block_number = efa_block_number_for_hour(hour)
        assert efa_block_label_for_index(block_number) == efa_block_label(hour)


def test_efa_block_number_for_hour_handles_the_wraparound_block():
    assert efa_block_number_for_hour(23) == 1
    assert efa_block_number_for_hour(0) == 1
    assert efa_block_number_for_hour(2) == 1
    assert efa_block_number_for_hour(3) == 2


def test_efa_block_number_for_hour_rejects_invalid_hour():
    with pytest.raises(ValueError):
        efa_block_number_for_hour(24)
    with pytest.raises(ValueError):
        efa_block_number_for_hour(-1)


# --- direction_from_sign ---


def test_direction_from_sign_positive_is_import():
    assert direction_from_sign(500.0) == "Import"


def test_direction_from_sign_negative_is_export():
    assert direction_from_sign(-500.0) == "Export"


def test_direction_from_sign_zero_is_import_by_tiebreak():
    assert direction_from_sign(0.0) == "Import"


# --- largest_value_by_group ---


def test_largest_value_by_group_finds_the_max_per_group():
    rows = [
        {"day": "2026-06-01", "value": 800},
        {"day": "2026-06-01", "value": 1200},  # max for this day
        {"day": "2026-06-01", "value": 400},
        {"day": "2026-06-02", "value": 900},
    ]
    result = largest_value_by_group(rows, group_field="day", value_field="value")
    assert result["2026-06-01"]["max_value"] == 1200.0
    assert result["2026-06-01"]["count"] == 3
    assert result["2026-06-02"]["max_value"] == 900.0
    assert result["2026-06-02"]["count"] == 1


def test_largest_value_by_group_does_not_take_absolute_value():
    """Values are used as given -- the caller is responsible for
    signing/filtering appropriately first."""
    rows = [{"day": "2026-06-01", "value": -1500}, {"day": "2026-06-01", "value": 200}]
    result = largest_value_by_group(rows, group_field="day", value_field="value")
    assert result["2026-06-01"]["max_value"] == 200.0  # not 1500 -- no abs() applied


def test_largest_value_by_group_skips_rows_missing_group_or_value():
    rows = [{"day": None, "value": 999}, {"day": "2026-06-01", "value": None}, {"day": "2026-06-01", "value": 100}]
    result = largest_value_by_group(rows, group_field="day", value_field="value")
    assert result == {"2026-06-01": {"max_value": 100.0, "count": 1}}


def test_largest_value_by_group_handles_empty_input():
    assert largest_value_by_group([], group_field="day", value_field="value") == {}


# --- seasonal_average_by_period ---


def test_seasonal_average_by_period_pools_across_days():
    rows = [
        {"settlementPeriod": 1, "price": 40.0},  # day 1
        {"settlementPeriod": 1, "price": 60.0},  # day 2
        {"settlementPeriod": 2, "price": 100.0},
    ]
    result = seasonal_average_by_period(rows, period_field="settlementPeriod", value_field="price")
    assert result[1] == 50.0  # (40+60)/2, pooled across both days
    assert result[2] == 100.0


def test_seasonal_average_by_period_excludes_zero_by_default():
    """The real, sourced MID data-quality issue: N2EX shows all-zero
    values for some dates. These must not silently drag the average
    toward zero."""
    rows = [{"settlementPeriod": 1, "price": 0.0}, {"settlementPeriod": 1, "price": 80.0}]
    result = seasonal_average_by_period(rows, period_field="settlementPeriod", value_field="price")
    assert result[1] == 80.0  # not 40.0 -- the zero row is excluded, not averaged in


def test_seasonal_average_by_period_can_disable_exclusion():
    rows = [{"settlementPeriod": 1, "price": 0.0}, {"settlementPeriod": 1, "price": 80.0}]
    result = seasonal_average_by_period(rows, period_field="settlementPeriod", value_field="price", exclude_values=set())
    assert result[1] == 40.0  # zero included this time -- caller explicitly opted out


def test_seasonal_average_by_period_omits_periods_with_no_surviving_data():
    rows = [{"settlementPeriod": 1, "price": 0.0}]  # only observation for period 1, and it's excluded
    result = seasonal_average_by_period(rows, period_field="settlementPeriod", value_field="price")
    assert 1 not in result  # absent, not zero-filled -- caller decides the fallback


def test_seasonal_average_by_period_skips_rows_missing_period_or_value():
    rows = [{"settlementPeriod": None, "price": 999}, {"settlementPeriod": 1, "price": None}, {"settlementPeriod": 1, "price": 50.0}]
    result = seasonal_average_by_period(rows, period_field="settlementPeriod", value_field="price")
    assert result == {1: 50.0}
