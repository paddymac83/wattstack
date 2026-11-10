"""Exact decomposition of an auction into independent sub-markets
(`clear_auction_by_component`) plus the solver time-limit plumbing.

Run on their own:  pytest tests/test_eac_components.py -v

The claim under test is strong: solving the sub-markets separately gives
the SAME welfare as one joint MILP, on markets with several independent
islands. Welfare (not the acceptance vector) is compared because two
optimal solutions can differ on ties; the independent rule checker is run
on the merged result as well.
"""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from test_eac_pricing import _general_random_market

from wattstack_ingestion.eac_auction import (
    BuyOrderInput,
    OverholdingMode,
    SellOrderInput,
    _components,
    clear_auction,
    clear_auction_by_component,
    verify_clearing,
)

W1 = (datetime(2026, 6, 15, 0, 0), datetime(2026, 6, 15, 4, 0))
W2 = (datetime(2026, 6, 15, 4, 0), datetime(2026, 6, 15, 8, 0))


def _s(order_id, unit, basket, otype, product, qty, price, window=W1, looped=None):
    return SellOrderInput(order_id, unit, basket, otype, product, qty, price, window[0], window[1], looped)


def _b(order_id, product, qty, price, window=W1, family=None):
    return BuyOrderInput(order_id, product, qty, price, window[0], window[1], family)


def _island(seed: int, index: int):
    """One random market, relabelled and shifted by `index` days so that it
    shares no unit, basket, order, family or (product, window) with any other
    island."""
    sells, buys = _general_random_market(seed)
    shift = timedelta(days=index)
    out_sells = [
        replace(
            so, order_id=f"i{index}_{so.order_id}", auction_unit=f"i{index}_{so.auction_unit}",
            basket_id=f"i{index}_{so.basket_id}",
            looped_basket_id=(f"i{index}_{so.looped_basket_id}" if so.looped_basket_id else None),
            window_start=so.window_start + shift, window_end=so.window_end + shift,
        )
        for so in sells
    ]
    out_buys = [
        replace(
            bo, order_id=f"i{index}_{bo.order_id}", window_start=bo.window_start + shift,
            window_end=bo.window_end + shift,
            substitutability_family=(f"i{index}_{bo.substitutability_family}" if bo.substitutability_family else None),
        )
        for bo in buys
    ]
    return out_sells, out_buys


# --- what couples orders ---------------------------------------------------


def test_orders_in_one_product_window_are_one_component():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0), _s("b", "U2", "B2", "PARENT", "DCL", 10, 2.0)]
    assert len(_components(sells, [_b("d", "DCL", 10, 9.0)])) == 1


def test_different_products_in_the_same_window_are_independent_without_a_shared_unit():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0), _s("b", "U2", "B2", "PARENT", "DCH", 10, 2.0)]
    buys = [_b("d1", "DCL", 10, 9.0), _b("d2", "DCH", 10, 9.0)]
    assert len(_components(sells, buys)) == 2


def test_one_unit_with_overlapping_baskets_in_two_products_is_one_component_rule_6():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0), _s("b", "U1", "B2", "PARENT", "DCH", 10, 2.0)]
    buys = [_b("d1", "DCL", 10, 9.0), _b("d2", "DCH", 10, 9.0)]
    assert len(_components(sells, buys)) == 1


def test_one_unit_in_non_overlapping_windows_is_two_components():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0, W1), _s("b", "U1", "B2", "PARENT", "DCL", 10, 2.0, W2)]
    buys = [_b("d1", "DCL", 10, 9.0, W1), _b("d2", "DCL", 10, 9.0, W2)]
    assert len(_components(sells, buys)) == 2


def test_looped_baskets_across_windows_are_one_component():
    sells = [
        _s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0, W1, looped="B2"),
        _s("b", "U1", "B2", "PARENT", "DCL", 10, 2.0, W2, looped="B1"),
    ]
    buys = [_b("d1", "DCL", 10, 9.0, W1), _b("d2", "DCL", 10, 9.0, W2)]
    assert len(_components(sells, buys)) == 1


def test_a_buy_family_links_otherwise_independent_products():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0), _s("b", "U2", "B2", "PARENT", "DCH", 10, 2.0)]
    buys = [_b("d1", "DCL", 10, 9.0, family="F"), _b("d2", "DCH", 10, 9.0, family="F")]
    assert len(_components(sells, buys)) == 1


def test_a_child_in_another_product_ties_its_basket_to_both_product_windows():
    sells = [
        _s("p", "U1", "B1", "PARENT", "DCL", 10, 1.0),
        _s("c", "U1", "B1", "CHILD", "DCH", 10, 2.0),
        _s("x", "U2", "B2", "PARENT", "DCH", 10, 2.0),
    ]
    buys = [_b("d1", "DCL", 10, 9.0), _b("d2", "DCH", 20, 9.0)]
    assert len(_components(sells, buys)) == 1


# --- exactness -------------------------------------------------------------


@pytest.mark.parametrize("mode", [OverholdingMode.RESTRICTED, OverholdingMode.PERMISSIVE])
@pytest.mark.parametrize("seeds", [(1, 2, 3), (10, 11, 12, 13), (40, 41), (77, 78, 79, 80, 81)])
def test_decomposed_welfare_equals_the_joint_welfare_on_multi_island_markets(seeds, mode):
    sells, buys = [], []
    for index, seed in enumerate(seeds):
        s, b = _island(seed, index)
        sells += s
        buys += b
    joint = clear_auction(sells, buys, overholding_mode=mode)
    parts = clear_auction_by_component(sells, buys, overholding_mode=mode)
    assert parts.welfare == pytest.approx(joint.welfare, abs=1e-6)
    assert parts.n_subproblems >= len(seeds)
    assert verify_clearing(sells, buys, parts, overholding_mode=mode) == []
    assert set(parts.clearing_price_by_product_window) == set(joint.clearing_price_by_product_window)
    assert set(parts.acceptance_ratio_by_order_id) == {so.order_id for so in sells}
    assert set(parts.buy_acceptance_ratio_by_order_id) == {bo.order_id for bo in buys}


@pytest.mark.parametrize("seed", range(40))
def test_decomposed_welfare_equals_the_joint_welfare_on_single_random_markets(seed):
    sells, buys = _general_random_market(seed)
    assert clear_auction_by_component(sells, buys).welfare == pytest.approx(clear_auction(sells, buys).welfare, abs=1e-6)


def test_islands_report_more_than_one_subproblem_and_sum_their_solve_time():
    sells, buys = [], []
    for index, seed in enumerate((5, 6, 7)):
        s, b = _island(seed, index)
        sells += s
        buys += b
    result = clear_auction_by_component(sells, buys)
    assert result.n_subproblems >= 3
    assert result.solve_seconds > 0.0
    assert result.optimality_proven is True


def test_global_price_bounds_are_shared_by_every_subproblem():
    """An island with only cheap orders must still use the market-wide upper
    bound, not its own: the cheap island's idle-window price is clipped to the
    global range, exactly as the joint solve does."""
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0), _s("b", "U2", "B2", "PARENT", "DCH", 10, 90.0)]
    buys = [_b("d1", "DCL", 10, 5.0), _b("d2", "DCH", 10, 100.0)]
    parts = clear_auction_by_component(sells, buys)
    assert parts.price_bounds == (0.0, 100.0)
    assert parts.n_subproblems == 2
    assert parts.welfare == pytest.approx(clear_auction(sells, buys).welfare)


def test_empty_market():
    result = clear_auction_by_component([], [])
    assert result.n_subproblems == 0
    assert result.acceptance_ratio_by_order_id == {}


def test_validation_is_global_not_per_component():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0), _s("a", "U2", "B2", "PARENT", "DCH", 10, 2.0)]
    with pytest.raises(ValueError, match="duplicate sell order_id"):
        clear_auction_by_component(sells, [])


def test_explicit_price_bounds_still_reject_out_of_range_orders():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 50.0)]
    with pytest.raises(ValueError, match="outside price_bounds"):
        clear_auction_by_component(sells, [_b("d", "DCL", 10, 60.0)], price_bounds=(0.0, 10.0))


# --- solver limits ---------------------------------------------------------


def test_time_limit_and_gap_are_accepted_and_a_small_market_is_still_proven_optimal():
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 1.0)]
    buys = [_b("d", "DCL", 10, 9.0)]
    result = clear_auction(sells, buys, time_limit_s=30, mip_gap=0.0)
    assert result.optimality_proven is True
    assert result.solve_seconds >= 0.0
    assert result.welfare == pytest.approx(80.0)


# --- rule 6 semantics: chained, nested and back-to-back windows --------------
# One unit, one product, baskets on different windows, each with its own 10 MW
# (or 40 MW) buyer at 10 GBP/MW/h and a seller limit of 1: a basket of q MW is
# worth q * 9 on its own. Rule 6: a unit can win only one of any two baskets
# whose windows overlap.

H = datetime(2026, 6, 15, 0, 0)


def _unit_basket(name, start_h, end_h, qty):
    window = (H + timedelta(hours=start_h), H + timedelta(hours=end_h))
    return (
        _s(f"p{name}", "U", f"B{name}", "PARENT", "DCL", qty, 1.0, window),
        _b(f"d{name}", "DCL", qty, 10.0, window),
    )


def _clear_unit(spec):
    sells, buys = [], []
    for name, start_h, end_h, qty in spec:
        s, b = _unit_basket(name, start_h, end_h, qty)
        sells.append(s)
        buys.append(b)
    result = clear_auction(sells, buys)
    assert verify_clearing(sells, buys, result) == []
    return {n: result.acceptance_ratio_by_order_id[f"p{n}"] for n, *_ in spec}, result.welfare


@pytest.mark.parametrize("b_qty,expected,welfare", [
    (10, {"A": 1.0, "B": 0.0, "C": 1.0}, 180.0),  # A [0,4) and C [4,8) do not overlap: both win (2 x 90)
    (40, {"A": 0.0, "B": 1.0, "C": 0.0}, 360.0),  # B [2,6) overlaps both and is worth 40 x 9
])
def test_chained_overlaps_exclude_only_the_neighbours(b_qty, expected, welfare):
    ratios, w = _clear_unit([("A", 0, 4, 10), ("B", 2, 6, b_qty), ("C", 4, 8, 10)])
    assert ratios == expected and w == pytest.approx(welfare)


@pytest.mark.parametrize("a_qty,expected,welfare", [
    (10, {"A": 0.0, "B": 1.0, "C": 1.0}, 180.0),  # B [1,3) and C [5,7) sit inside A [0,8) but not each other
    (40, {"A": 1.0, "B": 0.0, "C": 0.0}, 360.0),
])
def test_nested_overlaps_let_the_inner_baskets_win_together_or_the_outer_alone(a_qty, expected, welfare):
    ratios, w = _clear_unit([("A", 0, 8, a_qty), ("B", 1, 3, 10), ("C", 5, 7, 10)])
    assert ratios == expected and w == pytest.approx(welfare)


def test_back_to_back_windows_do_not_overlap():
    ratios, w = _clear_unit([("A", 0, 4, 10), ("B", 4, 8, 10)])
    assert ratios == {"A": 1.0, "B": 1.0} and w == pytest.approx(180.0)


def test_many_identical_windows_allow_exactly_one_winner():
    ratios, w = _clear_unit([(n, 0, 4, 10) for n in "ABCDE"])
    assert sum(ratios.values()) == 1.0 and w == pytest.approx(90.0)


def test_explicit_price_bounds_reach_every_subproblem():
    """With bounds (5, 50) an idle buy-only window is its own sub-market; its
    reported price is 0.0 clipped into the bounds = 5, the same as in the
    joint solve. (If each sub-market re-derived its own bounds it would say 0.)"""
    sells = [_s("a", "U1", "B1", "PARENT", "DCL", 10, 6.0)]
    buys = [_b("d1", "DCL", 10, 40.0), _b("idle", "DCH", 10, 20.0)]
    parts = clear_auction_by_component(sells, buys, price_bounds=(5.0, 50.0))
    joint = clear_auction(sells, buys, price_bounds=(5.0, 50.0))
    idle_key = ("DCH", W1[0], W1[1])
    assert parts.n_subproblems == 2
    assert joint.clearing_price_by_product_window[idle_key] == pytest.approx(5.0)
    assert parts.clearing_price_by_product_window[idle_key] == pytest.approx(5.0)
    assert parts.price_bounds == (5.0, 50.0)
