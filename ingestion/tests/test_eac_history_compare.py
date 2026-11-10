"""Comparison of simulated vs published auctions: the four lenses
(reconciliation, real-outcome-vs-rules, welfare, prices/volumes).

Run on their own:  pytest tests/test_eac_history_compare.py -v

Hand-built scenarios use one tiny auction whose numbers are easy to check:
  sell S: 10 MW parent, limit 2        buy B: 10 MW, limit 9
  accepted together: welfare = 10*9 - 10*2 = 70, cleared at price 2.
"""
import csv
from datetime import date

import pytest
from test_eac_history import buy_rec, data_from, sell_rec, summary_rec

import wattstack_ingestion.eac_history_compare as cmp
from wattstack_ingestion.eac_auction import OverholdingMode
from wattstack_ingestion.eac_history import HistoryData, build_case, parse_buy_rows, parse_sell_rows, parse_summary_rows, parse_unit_rows
from wattstack_ingestion.eac_history_compare import (
    Tolerances,
    _ceil_cent,
    _violation_category,
    actual_outcome,
    check_actual,
    compare_auction,
    reconcile,
    render_report,
    run_day,
    simulate,
    welfare_of,
    write_outputs,
)
from wattstack_ingestion.eac_history_synth import synth_day

RESTRICTED, PERMISSIVE = OverholdingMode.RESTRICTED, OverholdingMode.PERMISSIVE


def agree_data(**over):
    """The real outcome equals the optimum: S and B both executed at price 2."""
    return data_from(
        sells=[sell_rec(**{"clearingPrice": "2", **over.get("sell", {})})],
        buys=[buy_rec(**{"clearingPrice": "2", **over.get("buy", {})})],
        summary=[summary_rec(**{"clearingPrice": "2", **over.get("summary", {})})],
    )


def compare(data, mode=RESTRICTED, units=(), **kw):
    case = build_case(1, data, **kw)
    return case, compare_auction(case, list(units), simulate(case, mode))


# ===========================================================================
# Helpers
# ===========================================================================


@pytest.mark.parametrize("raw,expected", [(10.331, 10.34), (10.33, 10.33), (10.3300000001, 10.33), (0.1 + 0.2, 0.3), (-1.234, -1.23), (0.0, 0.0)])
def test_published_prices_round_up_to_the_penny(raw, expected):
    assert _ceil_cent(raw) == pytest.approx(expected)


@pytest.mark.parametrize("message,category", [
    ("rule 8: baskets ('1',) accepted with total surplus -3", "rule 8"),
    ("rule 7: child 5 accepted with negative surplus", "rule 7"),
    ("volume balance DCL: sell 10 != buy 5", "volume balance"),
    ("price 12 for DCL outside bounds (0, 10)", "price outside bounds"),
    ("buy 7: paradoxically accepted but the buyer forbids it", "paradox flag violated"),
    ("restricted overholding: 2 paradoxically accepted buy orders in DCL: ['a', 'b']", "restricted overholding cap"),
    ("buy family 3 sums to 1.5", "buy family > 1"),
    ("sell 4: ratio 1.4 outside [0,1]", "ratio outside [0,1]"),
])
def test_violation_categories(message, category):
    assert _violation_category(message) == category


# ===========================================================================
# Lens 3+4: welfare, prices, volumes
# ===========================================================================


def test_agreement_is_reported_as_zero_gap_and_full_match():
    case, c = compare(agree_data())
    assert c.welfare_actual == pytest.approx(70.0) and c.welfare_sim == pytest.approx(70.0)
    assert c.stats["welfare_gap"] == pytest.approx(0.0)
    assert c.stats["price_match_rate"] == 1.0 and c.stats["volume_match_rate"] == 1.0
    assert c.findings == [] and c.rules.violations == []
    (w,) = c.window_rows
    assert w["sim_volume_mw"] == pytest.approx(10.0) and w["sim_price"] == pytest.approx(2.0)


def test_real_outcome_with_lower_welfare_gives_a_positive_gap_when_feasible():
    data = data_from(
        sells=[sell_rec(status="REJECTED", acceptanceRatio="0", executedQuantity="0", clearingPrice="0")],
        buys=[buy_rec(status="REJECTED", acceptanceRatio="0", executedQuantity="0", clearingPrice="0")],
        summary=[summary_rec(clearedVolume="0", clearingPrice="0")],
    )
    case, c = compare(data)
    assert c.welfare_actual == 0.0 and c.stats["welfare_gap"] == pytest.approx(70.0)
    assert c.rules.violations == []  # the real outcome is legal under wattstack's rules, just worse
    (w,) = c.window_rows
    assert w["volume_match"] is False and w["volume_diff_mw"] == pytest.approx(10.0)
    assert c.stats["volume_match_rate"] == 0.0
    assert [r["match"] for r in c.sell_rows] == [False] and c.sell_rows[0]["sim_ratio"] == 1.0
    assert c.stats["sell_mw_moved_vs_actual"] == pytest.approx(10.0)


def test_a_price_only_difference_is_localised_to_prices():
    data = agree_data(summary={"clearingPrice": "3"})
    case, c = compare(data)
    (w,) = c.window_rows
    assert w["volume_match"] is True and w["price_match"] is False
    assert w["price_diff"] == pytest.approx(-1.0)
    assert c.stats["welfare_gap"] == pytest.approx(0.0)
    assert "order_price_not_summary_price" in {f.kind for f in c.findings}  # the dataset disagrees with itself too


def test_idle_windows_are_not_counted_as_matches_or_mismatches():
    data = data_from(
        sells=[sell_rec(clearingPrice="2", status="REJECTED", acceptanceRatio="0", executedQuantity="0", priceLimit="50")],
        buys=[buy_rec(price="9", status="REJECTED", acceptanceRatio="0", executedQuantity="0", clearingPrice="0")],
        summary=[summary_rec(clearedVolume="0", clearingPrice="0")],
    )
    case, c = compare(data)
    (w,) = c.window_rows
    assert w["both_idle"] is True and w["price_diff"] is None
    assert c.stats["price_match_rate"] is None


def test_matching_is_by_tolerance_not_exact_equality():
    data = agree_data(summary={"clearingPrice": "2.01", "clearedVolume": "10.04"})
    case, c = compare(data)
    assert c.window_rows[0]["price_match"] is True  # within 0.011
    assert c.window_rows[0]["volume_match"] is True  # sim volume is 10; the published 10.04 is not used for sim


# ===========================================================================
# Lens 2: the real outcome checked against wattstack's rules
# ===========================================================================


def test_a_real_outcome_that_breaks_a_rule_is_named():
    # price 1 is below the lone parent's limit 2: rule 8 (non-negative surplus)
    case, c = compare(agree_data(summary={"clearingPrice": "1"}, sell={"clearingPrice": "1"}, buy={"clearingPrice": "1"}))
    assert c.rules.by_category == {"rule 8": 1}


def test_inconsistent_volumes_in_the_real_outcome_are_a_volume_balance_violation():
    data = agree_data(buy={"acceptanceRatio": "0.5", "executedQuantity": "5"})
    case, c = compare(data)
    assert c.rules.by_category.get("volume balance") == 1
    assert "sell_volume_not_buy_volume" in {f.kind for f in c.findings}


def test_buyer_forbidding_paradox_but_accepted_above_its_limit_is_flagged():
    data = data_from(
        sells=[sell_rec(priceLimit="3", clearingPrice="3")],
        buys=[buy_rec(price="1", paradoxicallyAcceptanceAllowed="FALSE", clearingPrice="3")],
        summary=[summary_rec(clearingPrice="3")],
    )
    case, c = compare(data)
    assert "paradox flag violated" in c.rules.by_category


def _two_paradoxical_buyers():
    # sell 20 MW @3; two buyers whose limits (1 and 2) are below the price 3 are both accepted
    return data_from(
        sells=[sell_rec(quantity="20", priceLimit="3", executedQuantity="20", clearingPrice="3")],
        buys=[buy_rec(orderID="501", quantity="10", price="1", clearingPrice="3"),
              buy_rec(orderID="502", quantity="10", price="2", clearingPrice="3")],
        summary=[summary_rec(clearedVolume="20", clearingPrice="3")],
    )


def test_two_paradoxical_buyers_in_one_window_point_to_permissive_overholding():
    case = build_case(1, _two_paradoxical_buyers())
    rules = check_actual(case, actual_outcome(case), Tolerances().rule_check)
    assert rules.violations == []  # legal if any number of paradoxical buyers is allowed
    assert len(rules.cap_violations) == 1 and "restricted overholding" in rules.cap_violations[0]
    assert rules.paradox_histogram[2] == 1
    run = run_day(_two_paradoxical_buyers(), date(2026, 5, 7))
    assert "NESO behaves like PERMISSIVE overholding" in render_report(run)


def test_one_paradoxical_buyer_is_consistent_with_restricted_overholding():
    run = run_day(_one_paradoxical_buyer(), date(2026, 5, 7))
    assert "Consistent with RESTRICTED overholding" in render_report(run)


def _one_paradoxical_buyer():
    return data_from(
        sells=[sell_rec(quantity="10", priceLimit="3", executedQuantity="10", clearingPrice="3")],
        buys=[buy_rec(quantity="10", price="1", clearingPrice="3")],
        summary=[summary_rec(clearedVolume="10", clearingPrice="3")],
    )


# ===========================================================================
# Lens 1: reconciliation of the dataset against itself
# ===========================================================================


def kinds(data, units=()):
    case = build_case(1, data)
    return {f.kind for f in reconcile(case, list(units), Tolerances())}


def test_clean_data_reconciles():
    assert kinds(agree_data()) == set()


def test_executed_quantity_must_equal_quantity_times_ratio():
    assert "executed_not_quantity_x_ratio" in kinds(agree_data(sell={"executedQuantity": "7"}))


def test_summary_volume_must_match_the_orders():
    assert "summary_volume_not_order_volume" in kinds(agree_data(summary={"clearedVolume": "12"}))


def test_a_window_with_volume_but_no_summary_row_is_reported():
    data = data_from(sells=[sell_rec(clearingPrice="2")], buys=[buy_rec(clearingPrice="2")], summary=[])
    assert "window_missing_from_summary" in kinds(data)


def test_duplicate_summary_rows_are_reported():
    data = data_from(sells=[sell_rec()], buys=[buy_rec()], summary=[summary_rec(), summary_rec()])
    assert "duplicate_summary_rows" in kinds(data)


def test_a_fractional_parent_ratio_is_reported():
    assert "parent_ratio_fractional" in kinds(agree_data(sell={"acceptanceRatio": "0.5", "executedQuantity": "5"}))


def test_excluded_orders_with_volume_are_reported():
    data = data_from(
        sells=[sell_rec(), sell_rec(orderID="9", basketID="90", status="EXCLUDED", acceptanceRatio="0", executedQuantity="4")],
        buys=[buy_rec()], summary=[summary_rec()],
    )
    assert "excluded_row_has_volume" in kinds(data)


def test_results_by_unit_is_checked_against_the_order_totals():
    unit = {"auctionUnit": "U1", "auctionProduct": "DCL", "executedQuantity": "10", "clearingPrice": "5",
            "deliveryStart": "2026-05-06T22:00:00", "deliveryEnd": "2026-05-07T02:00:00"}
    good = parse_unit_rows([unit])
    bad = parse_unit_rows([{**unit, "executedQuantity": "6"}])
    case = build_case(1, agree_data())
    assert reconcile(case, good, Tolerances()) == []
    assert {f.kind for f in reconcile(case, bad, Tolerances())} == {"unit_result_not_order_volume"}


# ===========================================================================
# Error handling and knobs
# ===========================================================================


def test_a_failed_clearing_is_recorded_not_raised(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(cmp, "clear_auction_by_component", boom)
    case, c = compare(agree_data())
    assert c.sim.result is None and "solver exploded" in c.sim.error
    assert c.window_rows and "sim_price" not in c.window_rows[0] and c.sell_rows == []
    run = run_day(agree_data(), date(2026, 5, 7))
    assert "FAILED to clear" in render_report(run)


def test_price_bounds_that_exclude_an_order_surface_as_a_failed_clearing():
    run = run_day(agree_data(), date(2026, 5, 7), price_bounds=(0.0, 5.0))  # buy limit is 9
    (c,) = run.comparisons
    assert c.sim.result is None and "outside price_bounds" in c.sim.error


def test_excluded_policy_changes_the_simulated_market():
    sells = [
        sell_rec(priceLimit="5", clearingPrice="5"),
        sell_rec(orderID="101", basketID="11", auctionUnit="U2", priceLimit="1", status="EXCLUDED", acceptanceRatio="0",
                 executedQuantity="0", clearingPrice="0"),
    ]
    data = data_from(sells=sells, buys=[buy_rec(clearingPrice="5")], summary=[summary_rec(clearingPrice="5")])
    dropped = run_day(data, date(2026, 5, 7), excluded="drop").comparisons[0]
    kept = run_day(data, date(2026, 5, 7), excluded="keep").comparisons[0]
    assert dropped.welfare_sim == pytest.approx(40.0)  # only the 5-limit seller exists
    assert kept.welfare_sim == pytest.approx(80.0)  # the excluded 1-limit seller would have won


def test_time_limit_and_gap_are_passed_through_and_a_small_market_is_proven_optimal():
    run = run_day(agree_data(), date(2026, 5, 7), time_limit_s=30, mip_gap=0.0)
    assert run.comparisons[0].sim.result.optimality_proven is True


def test_no_auctions_in_the_day_is_an_error():
    with pytest.raises(ValueError, match="no auctions found"):
        run_day(agree_data(), date(2026, 5, 20))


def test_unmodelled_feature_windows_are_scored_separately():
    # two independent windows (different units, products); a flexible-group order marks NSR only,
    # and NESO's published NSR price differs from what a plain clearing gives.
    nsr_window = {"deliveryStart": "2026-05-06T22:00:00", "deliveryEnd": "2026-05-06T22:30:00", "auctionProduct": "NSR"}
    data = data_from(
        sells=[
            sell_rec(clearingPrice="2"),
            sell_rec(orderID="200", basketID="20", auctionUnit="U2", flexibleGroupID="5", clearingPrice="4", **nsr_window),
        ],
        buys=[buy_rec(clearingPrice="2"), buy_rec(orderID="600", clearingPrice="4", **nsr_window)],
        summary=[summary_rec(clearingPrice="2"), summary_rec(clearingPrice="4", **nsr_window)],
    )
    case, c = compare(data)
    s = c.stats
    assert s["windows_clean"] == 1 and s["windows_flagged"] == 1
    assert s["price_match_rate_clean"] == 1.0
    assert s["price_match_rate_flagged"] == 0.0  # sim prices NSR at 2, NESO published 4
    assert s["price_match_rate"] == pytest.approx(0.5)


# ===========================================================================
# Round trip on synthetic days (all 12 products)
# ===========================================================================


def _history(day=date(2026, 5, 7), seed=0, **kw):
    raw = synth_day(day, seed, **kw)
    return HistoryData(
        parse_sell_rows(raw["sell_orders"]), parse_buy_rows(raw["buy_orders"]),
        parse_summary_rows(raw["results_summary"]), parse_unit_rows(raw["results_by_unit"]),
    )


@pytest.mark.parametrize("seed", range(12))
def test_pipeline_reproduces_a_synthetic_published_day_exactly(seed):
    data = _history(seed=seed)
    run = run_day(data, date(2026, 5, 7))
    (c,) = run.comparisons
    assert c.sim.error is None
    assert c.stats["welfare_gap"] == pytest.approx(0.0, abs=1e-6)
    assert c.stats["price_match_rate"] == 1.0 and c.stats["volume_match_rate"] == 1.0
    assert c.rules.violations == [] and c.findings == []
    assert c.case.issues == []


def test_synthetic_days_exercise_all_twelve_products():
    seen = set()
    for seed in range(12):
        seen |= {r.product for r in _history(seed=seed).sells}
    assert seen == {"DRL", "DRH", "DML", "DMH", "DCL", "DCH", "NSR", "PSR", "NQR", "PQR", "NBR", "PBR"}


def test_permissive_mode_is_never_worse_in_welfare_on_synthetic_days():
    for seed in range(6):
        run = run_day(_history(seed=seed), date(2026, 5, 7), modes=(RESTRICTED, PERMISSIVE))
        r, p = run.comparisons
        assert p.welfare_sim >= r.welfare_sim - 1e-6


def test_unmodelled_markers_on_a_synthetic_day_are_detected_and_do_not_change_the_outcome():
    run = run_day(_history(seed=1, with_unmodelled_features=True), date(2026, 5, 7))
    (c,) = run.comparisons
    assert c.case.census.unmodelled  # flagged
    assert c.stats["windows_flagged"] >= 1
    assert c.stats["price_match_rate"] == 1.0  # the markers were added after clearing


def test_breaking_one_published_order_is_caught_by_three_lenses():
    raw = synth_day(date(2026, 5, 7), seed=3)
    victim = next(r for r in raw["sell_orders"] if r["status"] == "EXECUTED" and r["orderType"] == "PARENT")
    victim.update(status="REJECTED", acceptanceRatio=0.0, executedQuantity=0.0, clearingPrice=0.0)
    data = HistoryData(parse_sell_rows(raw["sell_orders"]), parse_buy_rows(raw["buy_orders"]),
                       parse_summary_rows(raw["results_summary"]), parse_unit_rows(raw["results_by_unit"]))
    (c,) = run_day(data, date(2026, 5, 7)).comparisons
    assert c.findings  # reconciliation: sell volume no longer equals buy volume / summary
    assert c.rules.violations  # rules: volume balance broken in the real outcome
    # wattstack still clears the victim, so at order level it disagrees with the (broken) published row
    assert any(not r["match"] and r["order_id"] == victim["orderID"] for r in c.sell_rows)
    assert c.stats["sell_order_agreement"] < 1.0


def test_welfare_of_matches_the_engine_welfare():
    case = build_case(1, agree_data())
    a = actual_outcome(case)
    assert welfare_of(case, a.sell_ratio, a.buy_ratio) == pytest.approx(70.0)


# ===========================================================================
# Output files
# ===========================================================================


def test_outputs_have_one_row_per_window_per_mode_and_a_full_report(tmp_path):
    run = run_day(_history(seed=2), date(2026, 5, 7), modes=(RESTRICTED, PERMISSIVE))
    report = write_outputs(run, tmp_path)
    n_windows = len(run.comparisons[0].window_rows)
    with open(tmp_path / "windows.csv") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2 * n_windows and {r["mode"] for r in rows} == {"restricted", "permissive"}
    with open(tmp_path / "auctions.csv") as fh:
        assert len(list(csv.DictReader(fh))) == 2
    with open(tmp_path / "sell_orders.csv") as fh:
        assert len(list(csv.DictReader(fh))) == 2 * len(run.comparisons[0].sell_rows)
    text = report.read_text()
    for heading in ("## 1. Headline", "## 2. What the real outcome says", "## 3. By product", "## 4. Largest disagreements",
                    "## 5. Does the published data agree", "## 6. What is in the data", "## 7. How to read this"):
        assert heading in text
    assert "|price diff|" not in text  # a pipe inside a table cell would break the markdown table
    assert (tmp_path / "run.json").exists()


def test_report_flags_data_issues_and_product_subsets():
    sells = [sell_rec(status="EXCLUDED", acceptanceRatio="0", executedQuantity="0"), sell_rec(orderID="101", orderType="CHILD")]
    data = data_from(sells=sells + [sell_rec(orderID="200", basketID="20", auctionUnit="U2")], buys=[buy_rec()], summary=[summary_rec()])
    text = render_report(run_day(data, date(2026, 5, 7), products=["DCL"]))
    assert "PRODUCT SUBSET" in text and "basket_without_single_parent" in text
