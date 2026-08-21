import pytest

from wattstack_ingestion.analysis import (
    aggregate_volume_by_day_and_category,
    bid_volume,
    bin_counts_by_group,
    bucket_start_for_value,
    classify_system_length,
    DC_ACTIVATION_RECOVERY_WINDOW_PERIODS,
    DC_ASSESSMENT_PERIOD,
    DC_RECOVERY_GATE_PERIODS,
    DC_RECOVERY_PERIODS_FROM_EMPTY,
    dc_activation_risk_premium,
    dc_bid_floor_price,
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
    probability_by_bin,
    seasonal_average_by_period,
    shrink_probability_by_bin,
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


# --- probability_by_bin ---


def test_probability_by_bin_computes_correct_empirical_probabilities():
    # bin 28000-29000: 3 Short, 1 Long -> P(Short)=0.75, P(Long)=0.25
    bin_values = [28100, 28200, 28300, 28400]
    group_values = ["Short", "Short", "Short", "Long"]
    result = probability_by_bin(bin_values, group_values, bin_width=1000.0)
    bucket = result[28000.0]
    assert bucket["Short"] == 0.75
    assert bucket["Long"] == 0.25


def test_probability_by_bin_probabilities_sum_to_one_per_bucket():
    bin_values = [1.0, 2.0, 3.0, 11.0, 12.0]
    group_values = ["A", "B", "A", "B", "B"]
    result = probability_by_bin(bin_values, group_values, bin_width=10.0)
    for bucket in result.values():
        assert abs(sum(bucket.values()) - 1.0) < 1e-9


def test_probability_by_bin_omits_buckets_with_zero_observations():
    """bin_counts_by_group() would report a zero-count bucket for any
    gap between the min and max -- probability_by_bin() must not
    return that bucket with an undefined (0/0) probability."""
    bin_values = [1.0, 31.0]  # a big gap -- middle buckets have no data
    group_values = ["A", "B"]
    result = probability_by_bin(bin_values, group_values, bin_width=10.0)
    assert 10.0 not in result  # the empty middle bucket is absent, not present with garbage


def test_probability_by_bin_handles_empty_input():
    assert probability_by_bin([], [], bin_width=10.0) == {}


# --- shrink_probability_by_bin ---


def test_shrink_probability_by_bin_barely_moves_a_well_populated_bucket():
    """A bucket with many real observations should be dominated by
    its own data -- shrinkage should have only a small effect, not
    override a genuine, well-supported signal."""
    # 100 observations, 90 Short -- a strong, well-supported 90% rate
    bin_values = [5.0] * 100
    group_values = ["Short"] * 90 + ["Long"] * 10
    raw = probability_by_bin(bin_values, group_values, bin_width=10.0)
    shrunk = shrink_probability_by_bin(bin_values, group_values, bin_width=10.0, shrinkage_strength=10.0)
    assert abs(raw[0.0]["Short"] - shrunk[0.0]["Short"]) < 0.1  # close, not identical, but not wildly different


def test_shrink_probability_by_bin_pulls_a_thin_bucket_strongly_toward_the_overall_rate():
    """The actual point of this function: a bucket with only 2
    observations showing 100% Short by chance must NOT be trusted at
    face value -- it should be pulled substantially toward the
    dataset's overall rate."""
    # overall dataset: mostly Long (80%), except one thin bucket that happens to be 100% Short
    bin_values = [5.0, 5.0] + [50.0] * 20  # bucket 0-10: 2 obs; bucket 50-60: 20 obs
    group_values = ["Short", "Short"] + ["Long"] * 16 + ["Short"] * 4  # thin bucket: 100% Short; big bucket: 80% Long
    shrunk = shrink_probability_by_bin(bin_values, group_values, bin_width=10.0, shrinkage_strength=10.0)
    # the thin bucket's raw rate (100% Short) should be pulled well below 100% once shrunk
    assert shrunk[0.0]["Short"] < 0.7


def test_shrink_probability_by_bin_zero_strength_matches_the_raw_frequency_exactly():
    bin_values = [5.0, 6.0, 7.0]
    group_values = ["Short", "Short", "Long"]
    raw = probability_by_bin(bin_values, group_values, bin_width=10.0)
    shrunk = shrink_probability_by_bin(bin_values, group_values, bin_width=10.0, shrinkage_strength=0.0)
    assert raw[0.0]["Short"] == shrunk[0.0]["Short"]


def test_shrink_probability_by_bin_converges_to_the_unconditional_rate_as_strength_grows_large():
    """The actual mathematically-guaranteed property of shrinkage,
    unlike monotonic improvement on any single backtest (which is
    NOT guaranteed -- small test sets can make an unshrunk estimator
    look good by chance): as shrinkage_strength -> infinity, every
    bucket's estimate converges to exactly the dataset's overall rate,
    regardless of that bucket's own (possibly noisy) data."""
    # a thin bucket with a misleadingly extreme raw rate (100% Short)
    bin_values = [5.0, 5.0] + [50.0] * 20
    group_values = ["Short", "Short"] + ["Long"] * 16 + ["Short"] * 4  # overall rate: 6/22 Short = ~27.3%
    huge_strength = shrink_probability_by_bin(bin_values, group_values, bin_width=10.0, shrinkage_strength=1_000_000.0)
    assert abs(huge_strength[0.0]["Short"] - 6 / 22) < 1e-4


def test_shrink_probability_by_bin_probabilities_still_sum_to_one():
    bin_values = [1.0, 2.0, 11.0, 12.0, 13.0]
    group_values = ["A", "B", "A", "A", "B"]
    result = shrink_probability_by_bin(bin_values, group_values, bin_width=10.0, shrinkage_strength=5.0)
    for bucket in result.values():
        assert abs(sum(bucket.values()) - 1.0) < 1e-9


def test_shrink_probability_by_bin_handles_empty_input():
    assert shrink_probability_by_bin([], [], bin_width=10.0) == {}


def test_shrink_probability_by_bin_omits_buckets_with_zero_observations():
    bin_values = [1.0, 31.0]
    group_values = ["A", "B"]
    result = shrink_probability_by_bin(bin_values, group_values, bin_width=10.0)
    assert 10.0 not in result


# --- bucket_start_for_value ---


def test_bucket_start_for_value_finds_the_exact_bucket():
    assert bucket_start_for_value(28300, bin_width=1000.0, available_bucket_starts=[27000.0, 28000.0, 29000.0]) == 28000.0


def test_bucket_start_for_value_falls_back_to_nearest_when_value_out_of_training_range():
    """Tomorrow's forecast can genuinely fall outside the training
    window's observed range -- must degrade to the closest available
    evidence, not return nothing."""
    result = bucket_start_for_value(50000, bin_width=1000.0, available_bucket_starts=[27000.0, 28000.0, 29000.0])
    assert result == 29000.0  # the nearest bucket to a value far above anything trained on


def test_bucket_start_for_value_returns_none_for_empty_training_data():
    assert bucket_start_for_value(28300, bin_width=1000.0, available_bucket_starts=[]) is None


# --- dc_activation_risk_premium ---


def test_dc_recovery_window_is_confirmed_8_periods():
    """1 SP (idle assessment/submission) + 2 SPs (1-hour gate) + 5 SPs
    (100% / 20%-per-SP minimum recovery rate) -- the full sequence
    confirmed directly, correcting an earlier version of this test
    that missed the idle assessment period as a delay distinct from
    the gate."""
    assert DC_ASSESSMENT_PERIOD == 1
    assert DC_RECOVERY_GATE_PERIODS == 2
    assert DC_RECOVERY_PERIODS_FROM_EMPTY == 5
    assert DC_ACTIVATION_RECOVERY_WINDOW_PERIODS == 8


def test_minimum_energy_requirement_matches_the_documents_own_worked_example():
    """NESO's own example: 50MW contracted -> 12.5MWh minimum energy
    requirement (15 minutes at full power). A non-zero degradation
    cost is used deliberately -- a zero-cost case wouldn't actually
    prove the 12.5MWh figure is doing anything in the calculation."""
    # expected_cost_per_event = 1.0 * 12.5 * (1.0 + 0.0) = 12.5; / (50.0 * 0.5) = 0.5
    premium = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=1.0,
        wholesale_prices_during_recovery_window=[], activation_probability=1.0,
    )
    assert premium == 0.5


def test_dc_activation_risk_premium_scales_with_activation_probability():
    low = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=10.0,
        wholesale_prices_during_recovery_window=[50.0, 50.0], activation_probability=0.01,
    )
    high = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=10.0,
        wholesale_prices_during_recovery_window=[50.0, 50.0], activation_probability=0.1,
    )
    assert high == low * 10  # linear in activation_probability, by construction


def test_dc_activation_risk_premium_zero_probability_gives_zero_premium():
    premium = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=10.0,
        wholesale_prices_during_recovery_window=[40.0, 90.0], activation_probability=0.0,
    )
    assert premium == 0.0


def test_dc_activation_risk_premium_uses_price_spread_not_average():
    """The opportunity-cost term should reflect max-min (a volatility/
    range proxy), not the average price level -- two windows with the
    same mean but different spread must give different premiums."""
    narrow_spread = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=0.0,
        wholesale_prices_during_recovery_window=[60.0, 60.0], activation_probability=1.0,
    )
    wide_spread = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=0.0,
        wholesale_prices_during_recovery_window=[20.0, 100.0], activation_probability=1.0,
    )
    assert wide_spread > narrow_spread


def test_dc_activation_risk_premium_handles_empty_price_window():
    """No wholesale price data for the recovery window must degrade
    gracefully (spread=0, only degradation cost counted), not crash."""
    premium = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=5.0,
        wholesale_prices_during_recovery_window=[], activation_probability=1.0,
    )
    # expected_cost_per_event = 1.0 * 12.5 * (5.0 + 0.0) = 62.5; / (50.0 * 0.5) = 2.5
    assert premium == 2.5


def test_dc_activation_risk_premium_full_worked_example():
    """A complete, hand-calculable case, matching NESO's own 50MW
    example for the energy figure."""
    # minimum_energy_mwh = 50 * 0.25 = 12.5
    # price_spread = 90 - 40 = 50
    # expected_cost_per_event = 0.02 * 12.5 * (8.0 + 50.0) = 0.02 * 12.5 * 58.0 = 14.5
    # premium = 14.5 / (50.0 * 0.5) = 0.58
    premium = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=8.0,
        wholesale_prices_during_recovery_window=[40.0, 65.0, 90.0, 55.0], activation_probability=0.02,
    )
    assert abs(premium - 0.58) < 1e-9


def test_dc_activation_risk_premium_respects_custom_settlement_period_hours():
    default_dt = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=10.0,
        wholesale_prices_during_recovery_window=[50.0], activation_probability=1.0,
    )
    custom_dt = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=10.0,
        wholesale_prices_during_recovery_window=[50.0], activation_probability=1.0,
        settlement_period_hours=1.0,
    )
    assert custom_dt == default_dt / 2  # doubling dt halves the per-hour premium


# --- dc_bid_floor_price ---


def test_dc_bid_floor_price_combines_baseline_opportunity_cost_and_activation_premium():
    """Hand-calculable: baseline opportunity cost from EFA-block price
    spread / 4h, plus the (separately already-tested) activation
    premium -- checked here as a genuine sum, not a black box."""
    # baseline: (90-40)/4.0 = 12.5
    # activation premium: 0.02 * (50*0.25) * (8.0+50.0) / (50*0.5) -- matches the full worked example already tested
    floor = dc_bid_floor_price(
        wholesale_prices_during_efa_block=[40.0, 65.0, 90.0, 55.0, 60.0, 70.0, 45.0, 80.0],
        contracted_mw=50.0, degradation_cost_per_mwh=8.0, activation_probability=0.02,
        wholesale_prices_during_recovery_window=[40.0, 65.0, 90.0, 55.0],
    )
    expected_baseline = (90.0 - 40.0) / 4.0
    expected_premium = 0.58  # from test_dc_activation_risk_premium_full_worked_example
    assert abs(floor - (expected_baseline + expected_premium)) < 1e-9


def test_dc_bid_floor_price_zero_when_flat_prices_and_no_activation_risk():
    floor = dc_bid_floor_price(
        wholesale_prices_during_efa_block=[50.0, 50.0, 50.0, 50.0],
        contracted_mw=50.0, degradation_cost_per_mwh=0.0, activation_probability=0.0,
        wholesale_prices_during_recovery_window=[50.0, 50.0],
    )
    assert floor == 0.0


def test_dc_bid_floor_price_scales_inversely_with_efa_block_hours():
    narrower_block = dc_bid_floor_price(
        wholesale_prices_during_efa_block=[40.0, 90.0], contracted_mw=50.0,
        degradation_cost_per_mwh=0.0, activation_probability=0.0,
        wholesale_prices_during_recovery_window=[], efa_block_hours=2.0,
    )
    wider_block = dc_bid_floor_price(
        wholesale_prices_during_efa_block=[40.0, 90.0], contracted_mw=50.0,
        degradation_cost_per_mwh=0.0, activation_probability=0.0,
        wholesale_prices_during_recovery_window=[], efa_block_hours=4.0,
    )
    assert narrower_block == wider_block * 2  # same spread, half the hours -> double the £/MW/h rate


def test_dc_bid_floor_price_handles_empty_efa_block_prices():
    floor = dc_bid_floor_price(
        wholesale_prices_during_efa_block=[], contracted_mw=50.0,
        degradation_cost_per_mwh=5.0, activation_probability=0.1,
        wholesale_prices_during_recovery_window=[],
    )
    # baseline term is 0.0 (no price data); only the activation premium contributes
    expected_premium = dc_activation_risk_premium(
        contracted_mw=50.0, degradation_cost_per_mwh=5.0,
        wholesale_prices_during_recovery_window=[], activation_probability=0.1,
    )
    assert floor == expected_premium
