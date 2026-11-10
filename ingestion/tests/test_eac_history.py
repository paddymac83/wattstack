"""Historical EAC data layer: parsing, day selection, case building,
census, download and the command line.

Run on their own:  pytest tests/test_eac_history.py -v
No network: the NESO client is replaced by an in-memory fake.
"""
from datetime import date, datetime

import pytest
from click.testing import CliRunner

from wattstack_ingestion.eac_history import (
    DATASET_FILES,
    HistoryData,
    HistoryDataError,
    build_case,
    day_bounds_utc,
    download_range,
    load_history_dir,
    parse_buy_rows,
    parse_sell_rows,
    parse_summary_rows,
    parse_unit_rows,
    tainted_keys,
)
from wattstack_ingestion.eac_history_cli import main as cli
from wattstack_ingestion.eac_history_synth import synth_day, write_synth

T0 = "2026-05-06T22:00:00"
T1 = "2026-05-07T02:00:00"


def sell_rec(**over):
    rec = {
        "auctionID": "1", "registeredAuctionParticipant": "P", "auctionUnit": "U1", "basketID": "10",
        "serviceType": "Response", "deliveryStart": T0, "deliveryEnd": T1, "orderID": "100", "orderType": "PARENT",
        "auctionProduct": "DCL", "productID": "1", "quantity": "10", "priceLimit": "2", "loopedBasketID": "",
        "orderEntryTime": T0, "acceptanceRatio": "1", "status": "EXECUTED", "executedQuantity": "10",
        "clearingPrice": "5", "reasonRejected": "", "flexibleGroupID": "", "minimumConsecutiveWindows": "",
    }
    rec.update(over)
    return rec


def buy_rec(**over):
    rec = {
        "auctionID": "1", "orderID": "500", "serviceType": "Response", "auctionProduct": "DCL", "deliveryStart": T0,
        "deliveryEnd": T1, "quantity": "10", "price": "9", "substitutabilityFamily": "",
        "paradoxicallyAcceptanceAllowed": "TRUE", "orderEntryTime": T0, "acceptanceRatio": "1", "status": "EXECUTED",
        "executedQuantity": "10", "clearingPrice": "5", "reasonRejected": "", "joinedFamily": "",
    }
    rec.update(over)
    return rec


def summary_rec(**over):
    rec = {
        "auctionID": "1", "auctionProduct": "DCL", "serviceType": "Response", "deliveryStart": T0, "deliveryEnd": T1,
        "clearedVolume": "10", "clearingPrice": "5", "linkedServiceWindowID": "",
    }
    rec.update(over)
    return rec


def data_from(sells=(), buys=(), summary=(), units=()):
    return HistoryData(
        parse_sell_rows(list(sells)), parse_buy_rows(list(buys)), parse_summary_rows(list(summary)),
        parse_unit_rows(list(units)),
    )


# ===========================================================================
# Parsing
# ===========================================================================


def test_sell_row_values_are_converted_from_csv_strings():
    (row,) = parse_sell_rows([sell_rec(loopedBasketID="11.0", flexibleGroupID="7", minimumConsecutiveWindows="4",
                                       reasonRejected="PRICE", orderType="Substitutable_Child", status="rejected")])
    assert row.auction_id == 1 and row.basket_id == "10" and row.order_id == "100"
    assert row.looped_basket_id == "11"  # "11.0" -> "11"
    assert row.flexible_group_id == "7" and row.minimum_consecutive_windows == 4
    assert row.order_type == "SUBSTITUTABLECHILD" and row.status == "REJECTED" and row.reason_rejected == "PRICE"
    assert row.quantity == 10.0 and row.price_limit == 2.0
    assert row.start == datetime(2026, 5, 6, 22, 0) and row.key == ("DCL", row.start, row.end)


def test_blank_optional_fields_become_none():
    (row,) = parse_sell_rows([sell_rec()])
    assert row.looped_basket_id is None and row.flexible_group_id is None
    assert row.minimum_consecutive_windows is None and row.reason_rejected is None


def test_timezone_aware_timestamps_are_converted_to_utc_naive():
    (row,) = parse_sell_rows([sell_rec(deliveryStart="2026-05-07T00:00:00+02:00", deliveryEnd="2026-05-07T04:00:00Z")])
    assert row.start == datetime(2026, 5, 6, 22, 0) and row.end == datetime(2026, 5, 7, 4, 0)


def test_native_json_types_from_the_api_are_accepted():
    (row,) = parse_sell_rows([sell_rec(auctionID=1, orderID=100, quantity=10.5, loopedBasketID=None, flexibleGroupID=None,
                                       minimumConsecutiveWindows=None, basketID=10.0)])
    assert row.quantity == 10.5 and row.basket_id == "10"


def test_column_names_are_matched_case_insensitively_and_a_bom_is_ignored():
    rec = {("﻿" + k if k == "auctionID" else k.lower()): v for k, v in sell_rec().items()}
    assert len(parse_sell_rows([rec])) == 1


def test_a_missing_required_column_is_named_in_the_error():
    rec = sell_rec()
    del rec["priceLimit"]
    with pytest.raises(HistoryDataError, match="priceLimit"):
        parse_sell_rows([rec])


def test_an_unparseable_value_reports_the_row():
    with pytest.raises(HistoryDataError, match="row 2"):
        parse_sell_rows([sell_rec(), sell_rec(orderID="101", quantity="ten")])


def test_unknown_status_and_order_type_are_rejected_not_guessed():
    with pytest.raises(HistoryDataError, match="unknown status"):
        parse_sell_rows([sell_rec(status="PENDING")])
    with pytest.raises(HistoryDataError, match="unknown orderType"):
        parse_sell_rows([sell_rec(orderType="GRANDCHILD")])


@pytest.mark.parametrize("raw,expected", [("TRUE", True), ("FALSE", False), ("true", True), (True, True), (False, False), ("0", False)])
def test_paradox_flag_formats(raw, expected):
    (row,) = parse_buy_rows([buy_rec(paradoxicallyAcceptanceAllowed=raw)])
    assert row.paradox_allowed is expected


def test_unparseable_paradox_flag_is_rejected():
    with pytest.raises(HistoryDataError, match="not a boolean"):
        parse_buy_rows([buy_rec(paradoxicallyAcceptanceAllowed="maybe")])


def test_buy_summary_and_unit_rows():
    (b,) = parse_buy_rows([buy_rec(substitutabilityFamily="3.0", joinedFamily="9")])
    assert b.family == "3" and b.joined_family == "9" and b.price == 9.0
    (s,) = parse_summary_rows([summary_rec(linkedServiceWindowID="2")])
    assert s.linked_service_window_id == "2" and s.cleared_volume == 10.0
    (u,) = parse_unit_rows([{"auctionUnit": "U1", "auctionProduct": "dcl", "executedQuantity": "4", "clearingPrice": "5",
                             "deliveryStart": T0, "deliveryEnd": T1}])
    assert u.unit == "U1" and u.product == "DCL" and u.executed_quantity == 4.0


def test_empty_input_parses_to_empty_list():
    assert parse_sell_rows([]) == [] and parse_buy_rows([]) == []


# ===========================================================================
# Day selection
# ===========================================================================


def test_efa_day_in_summer_time_runs_22_00_to_22_00_utc():
    assert day_bounds_utc(date(2026, 5, 7)) == (datetime(2026, 5, 6, 22, 0), datetime(2026, 5, 7, 22, 0))


def test_efa_day_in_winter_runs_23_00_to_23_00_utc():
    assert day_bounds_utc(date(2026, 1, 15)) == (datetime(2026, 1, 14, 23, 0), datetime(2026, 1, 15, 23, 0))


def test_clock_change_days_are_23_and_25_hours_long():
    a, b = day_bounds_utc(date(2026, 3, 29))  # spring forward
    assert (b - a).total_seconds() / 3600 == 23
    a, b = day_bounds_utc(date(2026, 10, 25))  # autumn back
    assert (b - a).total_seconds() / 3600 == 25


def test_calendar_basis_is_uk_midnight_to_midnight():
    assert day_bounds_utc(date(2026, 5, 7), "calendar") == (datetime(2026, 5, 6, 23, 0), datetime(2026, 5, 7, 23, 0))


def test_unknown_basis_is_rejected():
    with pytest.raises(ValueError, match="basis"):
        day_bounds_utc(date(2026, 5, 7), "weekly")


def test_auction_ids_are_found_from_any_dataset_within_the_day():
    data = data_from(
        sells=[sell_rec(auctionID="1")],
        buys=[buy_rec(auctionID="2", deliveryStart="2026-05-07T10:00:00", deliveryEnd="2026-05-07T14:00:00")],
        summary=[summary_rec(auctionID="3", deliveryStart="2026-05-09T10:00:00", deliveryEnd="2026-05-09T14:00:00")],
    )
    assert data.auction_ids_in(day_bounds_utc(date(2026, 5, 7))) == [1, 2]


# ===========================================================================
# Case building
# ===========================================================================


def _basic_data(**kw):
    return data_from(
        sells=kw.get("sells", [sell_rec()]), buys=kw.get("buys", [buy_rec()]), summary=kw.get("summary", [summary_rec()])
    )


def test_inputs_carry_only_input_columns_never_the_outcome():
    case = build_case(1, _basic_data(sells=[sell_rec(acceptanceRatio="0.37", executedQuantity="3.7", clearingPrice="99")]))
    (s,) = case.sell_inputs
    assert (s.order_id, s.auction_unit, s.basket_id, s.order_type, s.auction_product) == ("100", "U1", "10", "PARENT", "DCL")
    assert (s.quantity_mw, s.price_limit) == (10.0, 2.0)
    assert not hasattr(s, "acceptance_ratio") and not hasattr(s, "clearing_price")
    (b,) = case.buy_inputs
    assert (b.price, b.quantity_mw, b.paradoxical_acceptance_allowed) == (9.0, 10.0, True)


def test_buy_paradox_flag_and_family_reach_the_model():
    case = build_case(1, _basic_data(buys=[buy_rec(paradoxicallyAcceptanceAllowed="FALSE", substitutabilityFamily="4")]))
    (b,) = case.buy_inputs
    assert b.paradoxical_acceptance_allowed is False and b.substitutability_family == "4"


def test_excluded_orders_are_dropped_by_default_and_kept_on_request():
    sells = [sell_rec(), sell_rec(orderID="101", basketID="11", status="EXCLUDED", executedQuantity="0", acceptanceRatio="0")]
    assert [s.order_id for s in build_case(1, _basic_data(sells=sells)).sell_inputs] == ["100"]
    kept = build_case(1, _basic_data(sells=sells), excluded="keep")
    assert sorted(s.order_id for s in kept.sell_inputs) == ["100", "101"]


def test_invalid_excluded_policy_is_rejected():
    with pytest.raises(ValueError, match="excluded"):
        build_case(1, _basic_data(), excluded="maybe")


def test_an_excluded_parent_drops_its_whole_basket_and_is_reported():
    sells = [
        sell_rec(status="EXCLUDED", acceptanceRatio="0", executedQuantity="0"),
        sell_rec(orderID="101", orderType="CHILD"),
        sell_rec(orderID="200", basketID="20", auctionUnit="U2"),
    ]
    case = build_case(1, _basic_data(sells=sells))
    assert [s.order_id for s in case.sell_inputs] == ["200"]
    assert [i.kind for i in case.issues] == ["basket_without_single_parent"]


def test_strict_mode_raises_instead_of_quarantining():
    sells = [sell_rec(status="EXCLUDED", acceptanceRatio="0", executedQuantity="0"), sell_rec(orderID="101", orderType="CHILD")]
    with pytest.raises(HistoryDataError, match="without exactly one PARENT"):
        build_case(1, _basic_data(sells=sells), strict=True)


def test_a_basket_with_two_parents_is_quarantined():
    case = build_case(1, _basic_data(sells=[sell_rec(), sell_rec(orderID="101")]))
    assert case.sell_inputs == [] and case.issues[0].kind == "basket_without_single_parent"


def test_duplicate_order_ids_drop_every_copy_and_are_reported():
    case = build_case(1, _basic_data(sells=[sell_rec(), sell_rec(basketID="11"), sell_rec(orderID="200", basketID="20")]))
    assert [s.order_id for s in case.sell_inputs] == ["200"]
    assert any(i.kind == "duplicate_order_id" for i in case.issues)
    with pytest.raises(HistoryDataError, match="duplicate"):
        build_case(1, _basic_data(sells=[sell_rec(), sell_rec(basketID="11")]), strict=True)


def test_other_auctions_rows_are_not_mixed_in():
    sells = [sell_rec(), sell_rec(auctionID="2", orderID="300", basketID="30")]
    assert [s.order_id for s in build_case(1, _basic_data(sells=sells)).sell_inputs] == ["100"]


def test_product_subset_drops_other_products_and_baskets_whose_parent_is_gone():
    sells = [
        sell_rec(),  # DCL parent, basket 10
        sell_rec(orderID="101", orderType="CHILD", auctionProduct="NSR"),  # child in another product
        sell_rec(orderID="200", basketID="20", auctionUnit="U2", auctionProduct="NSR"),  # NSR parent
        sell_rec(orderID="201", basketID="20", auctionUnit="U2", orderType="CHILD"),  # DCL child of an NSR parent
    ]
    case = build_case(1, _basic_data(sells=sells), products=["dcl"])
    assert [s.order_id for s in case.sell_inputs] == ["100"]
    assert case.product_subset == ("DCL",)


def test_census_counts_statuses_reasons_products_and_structure():
    sells = [
        sell_rec(), sell_rec(orderID="101", orderType="CHILD"),
        sell_rec(orderID="200", basketID="20", auctionUnit="U2", status="REJECTED", acceptanceRatio="0",
                 executedQuantity="0", reasonRejected="NO_BUYER", loopedBasketID="99"),
    ]
    case = build_case(1, _basic_data(sells=sells, buys=[buy_rec(substitutabilityFamily="4")]))
    ce = case.census
    assert dict(ce.sell_status) == {"EXECUTED": 2, "REJECTED": 1}
    assert ce.reason_codes["sell REJECTED: NO_BUYER"] == 1
    assert ce.products == {"DCL": 1} and ce.n_keys == 1
    assert ce.n_looped_baskets == 1 and ce.n_dangling_loop_links == 1
    assert ce.n_buy_families == 1 and ce.n_components >= 1


def test_unmodelled_features_are_counted_in_the_census():
    sells = [
        sell_rec(auctionProduct="NSR", flexibleGroupID="5", minimumConsecutiveWindows="4"),
        sell_rec(orderID="200", basketID="20", auctionUnit="U2", auctionProduct="PSR", minimumConsecutiveWindows="2"),
    ]
    case = build_case(1, _basic_data(
        sells=sells, buys=[buy_rec(auctionProduct="NSR", joinedFamily="8")],
        summary=[summary_rec(auctionProduct="NSR", linkedServiceWindowID="1")],
    ))
    assert set(case.census.unmodelled) == {
        "flexible groups / minimumConsecutiveWindows (sell rows)", "joinedFamily (buy rows; meaning not documented)",
        "linked service windows (product-windows)",
    }
    assert case.flagged_sell_ids == {"100", "200"} and case.flagged_buy_ids == {"500"}


def test_clean_data_reports_nothing_unmodelled():
    assert build_case(1, _basic_data()).census.unmodelled == {}


def test_window_length_anomalies_are_reported_not_rejected():
    short = sell_rec(deliveryEnd="2026-05-06T22:30:00")  # DCL (Response) should be 4 h
    case = build_case(1, _basic_data(sells=[short], buys=[], summary=[]))
    assert case.census.window_length_anomalies == ["DCL: 30 min window (expected 240)"]
    assert len(case.sell_inputs) == 1


def test_unknown_product_codes_are_reported():
    case = build_case(1, _basic_data(sells=[sell_rec(auctionProduct="XYZ")], buys=[], summary=[]))
    assert case.census.unknown_products == ["XYZ"]


def test_tainting_travels_through_the_sub_market_but_stops_at_its_edge():
    sells = [
        sell_rec(auctionProduct="NSR", flexibleGroupID="5", deliveryEnd="2026-05-06T22:30:00"),  # flagged, key A
        sell_rec(orderID="200", basketID="20", auctionUnit="U1", auctionProduct="PSR",
                 deliveryEnd="2026-05-06T22:30:00"),  # same unit, overlapping window -> rule 6 couples it to key A
        sell_rec(orderID="300", basketID="30", auctionUnit="U9", auctionProduct="DCL"),  # unrelated
    ]
    case = build_case(1, _basic_data(sells=sells, buys=[], summary=[]))
    tainted_products = {k[0] for k in tainted_keys(case)}
    assert tainted_products == {"NSR", "PSR"}


def test_linked_window_keys_are_tainted_even_without_flagged_orders():
    case = build_case(1, _basic_data(summary=[summary_rec(linkedServiceWindowID="1")]))
    assert {k[0] for k in tainted_keys(case)} == {"DCL"}


# ===========================================================================
# Files
# ===========================================================================


def test_load_history_dir_roundtrips_a_synthetic_day(tmp_path):
    write_synth(tmp_path, date(2026, 5, 7), seed=2)
    data = load_history_dir(tmp_path)
    raw = synth_day(date(2026, 5, 7), seed=2)
    assert len(data.sells) == len(raw["sell_orders"]) and len(data.buys) == len(raw["buy_orders"])
    assert len(data.summary) == len(raw["results_summary"]) and len(data.units) == len(raw["results_by_unit"])
    assert (tmp_path / "README.txt").read_text().startswith("SYNTHETIC")


def test_missing_file_is_named(tmp_path):
    with pytest.raises(HistoryDataError, match="sell_orders.csv"):
        load_history_dir(tmp_path)


def test_results_by_unit_is_optional(tmp_path):
    write_synth(tmp_path, date(2026, 5, 7), seed=1)
    (tmp_path / DATASET_FILES["results_by_unit"]).unlink()
    assert load_history_dir(tmp_path).units == []


# ===========================================================================
# Download (fake client)
# ===========================================================================


class FakeClient:
    """Answers the SQL the downloader sends, from in-memory tables."""

    def __init__(self, tables):
        self.tables = tables  # resource id -> list of records
        self.sql_log = []

    def datastore_search_sql(self, sql):
        self.sql_log.append(sql)
        import re

        resource = re.search(r'FROM "([^"]+)"', sql).group(1)
        rows = list(self.tables[resource])
        m = re.search(r'"auctionID" IN \(([^)]*)\)', sql)
        if m:
            wanted = {x.strip() for x in m.group(1).split(",")}
            rows = [r for r in rows if str(r["auctionID"]) in wanted]
        lim = re.search(r"LIMIT (\d+) OFFSET (\d+)", sql)
        limit, offset = int(lim.group(1)), int(lim.group(2))
        return rows[offset: offset + limit]


def _fake_client():
    from wattstack_ingestion.neso import KNOWN_RESOURCES

    raw = synth_day(date(2026, 5, 7), seed=4)
    other_day = synth_day(date(2026, 5, 9), seed=5)
    return FakeClient({
        KNOWN_RESOURCES["eac_sell_orders"]: raw["sell_orders"] + other_day["sell_orders"],
        KNOWN_RESOURCES["eac_buy_orders"]: raw["buy_orders"] + other_day["buy_orders"],
        KNOWN_RESOURCES["response_reserve_results_summary"]: raw["results_summary"] + other_day["results_summary"],
        KNOWN_RESOURCES["response_reserve_results_by_unit"]: raw["results_by_unit"] + other_day["results_by_unit"],
    }), raw


def test_download_writes_only_the_requested_day_and_whole_auctions(tmp_path):
    client, raw = _fake_client()
    manifest = download_range(client, date(2026, 5, 7), date(2026, 5, 7), tmp_path, page_size=7)
    assert manifest["auction_ids"] == [20260507]
    assert manifest["rows"]["sell_orders"] == len(raw["sell_orders"])
    assert manifest["rows"]["buy_orders"] == len(raw["buy_orders"])
    data = load_history_dir(tmp_path)
    assert {r.auction_id for r in data.sells} == {20260507}
    assert (tmp_path / "manifest.json").exists()
    # pagination really paged (page_size 7 < rows)
    assert sum('FROM "13b511df' in q for q in client.sql_log) > 1


def test_download_queries_use_quoted_resource_ids_and_a_stable_order(tmp_path):
    client, _ = _fake_client()
    download_range(client, date(2026, 5, 7), date(2026, 5, 7), tmp_path)
    assert all(q.startswith('SELECT * FROM "') and 'ORDER BY "_id"' in q for q in client.sql_log)
    assert any('"auctionID" IN (20260507)' in q for q in client.sql_log)


def test_download_with_no_matching_results_is_an_error(tmp_path):
    client, _ = _fake_client()
    with pytest.raises(HistoryDataError, match="no rows delivering"):
        download_range(client, date(2026, 6, 1), date(2026, 6, 1), tmp_path)


def test_download_rejects_reversed_dates(tmp_path):
    client, _ = _fake_client()
    with pytest.raises(ValueError, match="precedes"):
        download_range(client, date(2026, 5, 8), date(2026, 5, 7), tmp_path)


# ===========================================================================
# Command line
# ===========================================================================


def test_cli_synth_inspect_run_end_to_end(tmp_path):
    runner = CliRunner()
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    r = runner.invoke(cli, ["synth", "--out", str(data_dir), "--day", "2026-05-07", "--seed", "1"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(cli, ["inspect", "--data", str(data_dir), "--day", "2026-05-07"])
    assert r.exit_code == 0 and "reconciliation: all checks passed" in r.output and "no violations" in r.output
    r = runner.invoke(cli, ["run", "--data", str(data_dir), "--day", "2026-05-07", "--out", str(out_dir), "--mode", "both"])
    assert r.exit_code == 0, r.output
    assert "[restricted]" in r.output and "[permissive]" in r.output and "price match 100.0%" in r.output
    for name in ("report.md", "auctions.csv", "windows.csv", "sell_orders.csv", "buy_orders.csv", "run.json"):
        assert (out_dir / name).exists()


def test_cli_reports_a_clear_error_for_a_day_without_auctions(tmp_path):
    runner = CliRunner()
    runner.invoke(cli, ["synth", "--out", str(tmp_path / "d"), "--day", "2026-05-07"])
    r = runner.invoke(cli, ["run", "--data", str(tmp_path / "d"), "--day", "2026-06-01", "--out", str(tmp_path / "o")])
    assert r.exit_code != 0 and "no auctions found" in r.output


def test_cli_reports_a_clear_error_for_a_missing_file(tmp_path):
    (tmp_path / "empty").mkdir()
    r = CliRunner().invoke(cli, ["inspect", "--data", str(tmp_path / "empty"), "--day", "2026-05-07"])
    assert r.exit_code != 0 and "sell_orders.csv" in r.output


def test_cli_product_subset_run_is_marked_as_a_smoke_test(tmp_path):
    runner = CliRunner()
    runner.invoke(cli, ["synth", "--out", str(tmp_path / "d"), "--day", "2026-05-07", "--seed", "1"])
    r = runner.invoke(cli, ["run", "--data", str(tmp_path / "d"), "--day", "2026-05-07", "--out", str(tmp_path / "o"),
                            "--products", "DCL,DCH"])
    assert r.exit_code == 0, r.output
    assert "PRODUCT SUBSET" in (tmp_path / "o" / "report.md").read_text()


# ===========================================================================
# Skipping rows from before the new format
# ===========================================================================


def test_rows_before_the_cutoff_are_skipped_even_if_they_are_in_an_old_layout(tmp_path):
    write_synth(tmp_path, date(2026, 5, 7), seed=1)
    path = tmp_path / DATASET_FILES["sell_orders"]
    lines = path.read_text().splitlines()
    header = lines[0].split(",")
    # an old-layout row: same columns, delivering in 2025, with a status the strict parser would reject
    old_row = {h: "" for h in header}
    old_row.update(deliveryStart="2025-01-01T00:00:00", deliveryEnd="2025-01-01T04:00:00", status="LEGACY")
    import csv as _csv
    with open(path, "a", newline="") as fh:
        _csv.DictWriter(fh, fieldnames=header).writerow(old_row)
    with pytest.raises(HistoryDataError, match="could not parse"):
        load_history_dir(tmp_path, since=None)  # reading everything trips on the old row
    data = load_history_dir(tmp_path)  # default cutoff 2026-05-06 skips it
    assert all(r.start.year == 2026 for r in data.sells)


def test_an_unreadable_delivery_start_is_not_silently_skipped(tmp_path):
    write_synth(tmp_path, date(2026, 5, 7), seed=1)
    path = tmp_path / DATASET_FILES["sell_orders"]
    text = path.read_text().replace("2026-05-06T22:00:00", "not-a-date", 1)
    path.write_text(text)
    with pytest.raises(HistoryDataError):
        load_history_dir(tmp_path)


def test_cli_since_option_is_accepted(tmp_path):
    runner = CliRunner()
    runner.invoke(cli, ["synth", "--out", str(tmp_path / "d"), "--day", "2026-05-07", "--seed", "1"])
    r = runner.invoke(cli, ["inspect", "--data", str(tmp_path / "d"), "--day", "2026-05-07", "--since", "2026-05-01"])
    assert r.exit_code == 0, r.output
