"""No real network calls -- requests.get is mocked throughout."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from wattstack_ingestion.eac_auction import (
    BuyOrderInput,
    ClearingResult,
    ResponseReserveOrdersProvider,
    SellOrderInput,
    backtest_hypothetical_bid,
    clear_auction,
    overholding_buy_order,
)
from wattstack_ingestion.neso import NesoClient

W1 = (datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0))
W2 = (datetime(2026, 6, 15, 16, 0), datetime(2026, 6, 15, 20, 0))  # non-overlapping with W1


def _sell(order_id, unit, basket, order_type, product, quantity, price, window=W1, looped_basket_id=None):
    return SellOrderInput(
        order_id=order_id, auction_unit=unit, basket_id=basket, order_type=order_type,
        auction_product=product, quantity_mw=quantity, price_limit=price,
        window_start=window[0], window_end=window[1], looped_basket_id=looped_basket_id,
    )


def _buy(order_id, product, quantity, price, window=W1, family=None):
    return BuyOrderInput(
        order_id=order_id, auction_product=product, quantity_mw=quantity, price=price,
        window_start=window[0], window_end=window[1], substitutability_family=family,
    )


# --- clear_auction: basic welfare maximisation (unchanged behaviour, new signature) ---


def test_cheaper_sell_order_accepted_over_more_expensive_one_when_volume_limited():
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0),
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 50.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 0.0


def test_accepted_sell_volume_exactly_matches_accepted_buy_volume():
    """Equality, not just <=, per the fix recorded in docs/adr/0030 --
    proven directly here for the new multi-window code path too, not
    just assumed carried over. Sell and buy volumes match exactly
    (20MW each) so a binary, non-curtailable PARENT order can actually
    achieve exact equality -- a mismatched pair (e.g. 100MW supply
    against 20MW demand) would make x=1 infeasible by construction,
    testing nothing about the equality constraint itself."""
    sell_orders = [_sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 20, 1.0)]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    accepted_mw = result.acceptance_ratio_by_order_id["S1"] * 20
    assert abs(accepted_mw - 20) < 1e-6


def test_sell_order_priced_above_all_buy_willingness_is_rejected():
    sell_orders = [_sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 500.0)]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 0.0


# --- mutual exclusivity: now based on genuine window overlap ---


def test_single_unit_two_baskets_same_window_only_one_accepted():
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCH", 10, 5.0, window=W1),
        _sell("S2", "UNIT_A", "B2", "PARENT", "DCL", 10, 5.0, window=W1),
    ]
    buy_orders = [_buy("BUY_H", "DCH", 10, 100.0, window=W1), _buy("BUY_L", "DCL", 10, 100.0, window=W1)]
    result = clear_auction(sell_orders, buy_orders)
    total = result.acceptance_ratio_by_order_id["S1"] + result.acceptance_ratio_by_order_id["S2"]
    assert total <= 1.0 + 1e-6


def test_single_unit_two_baskets_non_overlapping_windows_both_can_be_accepted():
    """The direct counterpart to the same-window test: mutual
    exclusivity is about window OVERLAP specifically, not merely
    "same unit" -- two of a unit's own baskets on genuinely
    non-overlapping windows should NOT constrain each other at all
    (absent a loop link, which is a separate, deliberate constraint
    tested elsewhere)."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0, window=W1),
        _sell("S2", "UNIT_A", "B2", "PARENT", "DCL", 10, 5.0, window=W2),
    ]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0, window=W1), _buy("BUY2", "DCL", 10, 100.0, window=W2)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 1.0


def test_different_units_are_not_mutually_exclusive():
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0),
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 5.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 1.0


# --- parent/child linkage and sell-side substitutable families (unchanged, new signature) ---


def test_child_order_cannot_be_accepted_if_parent_rejected():
    sell_orders = [
        _sell("PARENT1", "UNIT_A", "B1", "PARENT", "DCL", 10, 500.0),
        _sell("CHILD1", "UNIT_A", "B1", "CHILD", "DCL", 5, 1.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["PARENT1"] == 0.0
    assert result.acceptance_ratio_by_order_id["CHILD1"] == 0.0


def test_child_order_accepted_when_parent_is_accepted():
    sell_orders = [
        _sell("PARENT1", "UNIT_A", "B1", "PARENT", "DCL", 10, 1.0),
        _sell("CHILD1", "UNIT_A", "B1", "CHILD", "DCL", 5, 1.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["PARENT1"] == 1.0
    assert result.acceptance_ratio_by_order_id["CHILD1"] == 1.0


def test_sell_side_substitutable_children_sum_never_exceeds_one():
    sell_orders = [
        _sell("PARENT1", "UNIT_A", "B1", "PARENT", "DCL", 0, 1.0),
        _sell("SC1", "UNIT_A", "B1", "SUBSTITUTABLECHILD", "DCL", 10, 1.0),
        _sell("SC2", "UNIT_A", "B1", "SUBSTITUTABLECHILD", "DCL", 10, 1.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    total = result.acceptance_ratio_by_order_id["SC1"] + result.acceptance_ratio_by_order_id["SC2"]
    assert total <= 1.0 + 1e-6


# --- looped baskets ---


def test_looped_baskets_forced_to_reject_together_when_combined_welfare_negative():
    """Hand-constructed so the effect is unambiguous: B1 alone is
    clearly profitable (+950), B2 alone would be clearly unprofitable
    if forced (-1000 if accepted). Looped together, the combined
    welfare of accepting both (-50) is worse than rejecting both (0),
    so the loop must force B1 -- which would otherwise obviously have
    been accepted on its own -- to be rejected too."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0, window=W1, looped_basket_id="B2"),
        _sell("S2", "UNIT_A", "B2", "PARENT", "DCL", 10, 200.0, window=W2, looped_basket_id="B1"),
    ]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0, window=W1), _buy("BUY2", "DCL", 10, 100.0, window=W2)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 0.0
    assert result.acceptance_ratio_by_order_id["S2"] == 0.0


def test_looped_baskets_both_accepted_when_combined_welfare_positive():
    """The mirror case: B2 alone is only modestly profitable (+100 if
    it were unconstrained), well short of B1's own +950, but the
    combined loop is still net positive (+1050), so both should clear
    together."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0, window=W1, looped_basket_id="B2"),
        _sell("S2", "UNIT_A", "B2", "PARENT", "DCL", 10, 90.0, window=W2, looped_basket_id="B1"),
    ]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0, window=W1), _buy("BUY2", "DCL", 10, 100.0, window=W2)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 1.0


def test_unlooped_baskets_on_different_units_are_unaffected_by_looping_logic():
    """A basket with no looped_basket_id at all must behave exactly
    as if looping didn't exist -- proven by re-running the very first
    basic welfare-maximisation test through the same code path."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0),
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 50.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 0.0


# --- buy-side substitutability ---


def test_buy_side_substitutable_family_sum_never_exceeds_one():
    """Two cheap sell orders, one per product, would both be
    individually profitable -- but the two buy orders that would pay
    for them are in the same substitutability family, so only one
    (or a combination summing to at most 1) can actually be used."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCH", 10, 5.0),
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 5.0),
    ]
    buy_orders = [
        _buy("BUY_A", "DCH", 10, 100.0, family="FAM1"),
        _buy("BUY_B", "DCL", 10, 100.0, family="FAM1"),
    ]
    result = clear_auction(sell_orders, buy_orders)
    total_sell_accepted = result.acceptance_ratio_by_order_id["S1"] + result.acceptance_ratio_by_order_id["S2"]
    assert total_sell_accepted <= 1.0 + 1e-6


def test_buy_orders_without_a_family_are_not_constrained_by_substitutability():
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCH", 10, 5.0),
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 5.0),
    ]
    buy_orders = [_buy("BUY_A", "DCH", 10, 100.0), _buy("BUY_B", "DCL", 10, 100.0)]  # no family -- independent
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 1.0


# --- overholding ---


def test_overholding_buy_order_used_when_it_enables_a_negatively_priced_sell_order():
    """A negatively-priced sell order (the design document explicitly
    allows this) is profitable to accept regardless of what it's
    matched against, since accepting it ADDS to welfare directly. Real
    demand alone (5MW) isn't enough to absorb all 10MW of it -- the
    overholding order's extra headroom should let the rest clear."""
    sell_orders = [_sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, -10.0)]
    buy_orders = [
        _buy("BUY1", "DCL", 5, 50.0),
        overholding_buy_order("OVERHOLD", "DCL", max_overhold_mw=10, window_start=W1[0], window_end=W1[1]),
    ]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0  # fully accepted, using the overholding headroom


def test_overholding_buy_order_unused_when_sell_order_priced_positively():
    """The mirror case: with a normally-priced (positive) sell order,
    accepting more than real demand wants would only add cost with no
    offsetting utility -- the zero-priced overholding order must stay
    unused. Uses a CHILD (curtailable) sell order deliberately, not a
    binary PARENT: a binary order forces an all-or-nothing choice
    where accepting the full volume can still be genuinely
    welfare-positive on average (blending real demand's price with
    the zero-priced overholding volume) even when going beyond real
    demand alone wouldn't be -- that would test the wrong thing. A
    curtailable order lets the solver actually choose to stop exactly
    at real demand, which is the specific behaviour being verified
    here."""
    sell_orders = [
        _sell("PARENT1", "UNIT_A", "B1", "PARENT", "DCL", 0, 1.0),
        _sell("CHILD1", "UNIT_A", "B1", "CHILD", "DCL", 10, 20.0),
    ]
    buy_orders = [
        _buy("BUY1", "DCL", 5, 50.0),
        overholding_buy_order("OVERHOLD", "DCL", max_overhold_mw=10, window_start=W1[0], window_end=W1[1]),
    ]
    result = clear_auction(sell_orders, buy_orders)
    accepted_mw = result.acceptance_ratio_by_order_id["CHILD1"] * 10
    assert abs(accepted_mw - 5) < 1e-6  # only the real 5MW of demand, not the extra overholding headroom


def test_overholding_buy_order_helper_has_zero_price():
    order = overholding_buy_order("OH", "DCL", max_overhold_mw=15, window_start=W1[0], window_end=W1[1])
    assert order.price == 0.0
    assert order.quantity_mw == 15


# --- ClearingResult / hypothetical bid properties ---


def test_clearing_result_hypothetical_bid_accepted_true_when_ratio_positive():
    result = ClearingResult(
        acceptance_ratio_by_order_id={"HYPOTHETICAL_BID": 1.0}, clearing_price_by_product_window={},
        hypothetical_bid_order_id="HYPOTHETICAL_BID",
    )
    assert result.hypothetical_bid_accepted is True
    assert result.hypothetical_bid_acceptance_ratio == 1.0


def test_clearing_result_hypothetical_bid_accepted_false_when_ratio_zero():
    result = ClearingResult(
        acceptance_ratio_by_order_id={"HYPOTHETICAL_BID": 0.0}, clearing_price_by_product_window={},
        hypothetical_bid_order_id="HYPOTHETICAL_BID",
    )
    assert result.hypothetical_bid_accepted is False


def test_clearing_result_no_hypothetical_bid_id_gives_false_not_a_crash():
    result = ClearingResult(acceptance_ratio_by_order_id={}, clearing_price_by_product_window={})
    assert result.hypothetical_bid_accepted is False
    assert result.hypothetical_bid_acceptance_ratio == 0.0


def test_clearing_price_keyed_by_product_and_window_not_just_product():
    """Two windows of the same product must get their own, potentially
    different, clearing prices -- proven directly, not assumed."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0, window=W1),
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 40.0, window=W2),
    ]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0, window=W1), _buy("BUY2", "DCL", 10, 100.0, window=W2)]
    result = clear_auction(sell_orders, buy_orders)
    price_w1 = result.clearing_price_by_product_window[("DCL", W1[0], W1[1])]
    price_w2 = result.clearing_price_by_product_window[("DCL", W2[0], W2[1])]
    assert price_w1 == 5.0
    assert price_w2 == 40.0


def test_backtest_hypothetical_bid_cheap_bid_gets_accepted():
    orders_provider = MagicMock()
    orders_provider.sell_orders.return_value = [_sell("REAL1", "UNIT_A", "B1", "PARENT", "DCL", 10, 50.0)]
    orders_provider.buy_orders.return_value = [_buy("BUY1", "DCL", 30, 100.0)]

    result = backtest_hypothetical_bid(
        orders_provider, windows=[W1], auction_products=["DCL"], my_auction_unit="MY_UNIT",
        my_product="DCL", my_price=1.0, my_quantity_mw=5,
    )
    assert result.hypothetical_bid_accepted is True


def test_backtest_hypothetical_bid_expensive_bid_gets_rejected():
    orders_provider = MagicMock()
    orders_provider.sell_orders.return_value = [_sell("REAL1", "UNIT_A", "B1", "PARENT", "DCL", 30, 5.0)]
    orders_provider.buy_orders.return_value = [_buy("BUY1", "DCL", 30, 100.0)]

    result = backtest_hypothetical_bid(
        orders_provider, windows=[W1], auction_products=["DCL"], my_auction_unit="MY_UNIT",
        my_product="DCL", my_price=90.0, my_quantity_mw=5,
    )
    assert result.hypothetical_bid_accepted is False


def test_backtest_hypothetical_bid_fetches_every_window_passed_in():
    orders_provider = MagicMock()
    orders_provider.sell_orders.return_value = []
    orders_provider.buy_orders.return_value = []

    backtest_hypothetical_bid(
        orders_provider, windows=[W1, W2], auction_products=["DCH", "DCL"], my_auction_unit="MY_UNIT",
        my_product="DCL", my_price=10.0, my_quantity_mw=5,
    )
    fetched_windows = orders_provider.sell_orders.call_args_list[0].args[0]
    assert fetched_windows == [W1, W2]


def test_backtest_hypothetical_bid_uses_my_window_when_given_not_just_the_first_window():
    orders_provider = MagicMock()
    orders_provider.sell_orders.return_value = []
    orders_provider.buy_orders.return_value = [_buy("BUY1", "DCL", 5, 100.0, window=W2)]

    result = backtest_hypothetical_bid(
        orders_provider, windows=[W1, W2], auction_products=["DCL"], my_auction_unit="MY_UNIT",
        my_product="DCL", my_price=1.0, my_quantity_mw=5, my_window=W2,
    )
    assert result.hypothetical_bid_accepted is True  # only clears if it was actually placed in W2, matching BUY1


# --- ResponseReserveOrdersProvider: field mapping, multi-window fetch, new fields ---


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@patch("wattstack_ingestion.neso.requests.get")
def test_sell_orders_maps_confirmed_fields_including_looped_basket_id(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {
            "orderID": 7, "auctionUnit": "AG-GEDF02", "basketID": 3, "orderType": "PARENT",
            "auctionProduct": "DCL", "quantity": 5, "priceLimit": 12.5, "loopedBasketID": 9,
            "deliveryStart": "2026-06-15T08:00:00Z", "deliveryEnd": "2026-06-15T12:00:00Z",
        },
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.sell_orders([W1], "DCL")
    assert len(result) == 1
    assert result[0].order_id == "7"
    assert result[0].looped_basket_id == "9"


@patch("wattstack_ingestion.neso.requests.get")
def test_sell_orders_looped_basket_id_none_when_blank(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {
            "orderID": 1, "auctionUnit": "U1", "basketID": 1, "orderType": "PARENT",
            "auctionProduct": "DCL", "quantity": 5, "priceLimit": 1.0,
            "deliveryStart": "2026-06-15T08:00:00Z",
        },
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.sell_orders([W1], "DCL")
    assert result[0].looped_basket_id is None


@patch("wattstack_ingestion.neso.requests.get")
def test_sell_orders_fetches_across_multiple_windows(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"orderID": 1, "auctionUnit": "U1", "basketID": 1, "orderType": "PARENT",
         "auctionProduct": "DCL", "quantity": 5, "priceLimit": 1.0, "deliveryStart": "2026-06-15T08:00:00Z"},
        {"orderID": 2, "auctionUnit": "U2", "basketID": 2, "orderType": "PARENT",
         "auctionProduct": "DCL", "quantity": 5, "priceLimit": 1.0, "deliveryStart": "2026-06-15T16:00:00Z"},
        {"orderID": 3, "auctionUnit": "U3", "basketID": 3, "orderType": "PARENT",
         "auctionProduct": "DCL", "quantity": 5, "priceLimit": 1.0, "deliveryStart": "2026-06-16T08:00:00Z"},  # not in either window
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.sell_orders([W1, W2], "DCL")
    assert {r.order_id for r in result} == {"1", "2"}


@patch("wattstack_ingestion.neso.requests.get")
def test_buy_orders_maps_substitutability_family(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"orderID": 1, "auctionProduct": "DCH", "quantity": 10, "price": 100.0,
         "substitutabilityFamily": 4, "deliveryStart": "2026-06-15T08:00:00Z"},
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.buy_orders([W1], "DCH")
    assert result[0].substitutability_family == "4"


@patch("wattstack_ingestion.neso.requests.get")
def test_buy_orders_substitutability_family_none_when_blank(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"orderID": 1, "auctionProduct": "DCH", "quantity": 10, "price": 100.0,
         "deliveryStart": "2026-06-15T08:00:00Z"},
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.buy_orders([W1], "DCH")
    assert result[0].substitutability_family is None


# ===========================================================================
# Buy-side substitutability -- acceptance rules (Section 5.2, Appendix B)
#
# Run in isolation (from ingestion/):
#     pytest tests -k buyfam -v                       # these AND the pricing ones
#     pytest tests/test_eac_auction.py -k buyfam -v   # acceptance rules only
#
# The rule (p.14): "any combination of acceptance ratios between the
# different buy orders of the same family can be chosen by the algorithm,
# as long as the sum of the acceptance ratios of all orders in the same
# family does not exceed 100%". It is a constraint on RATIOS, not MW, and
# members may differ in volume, product and (overlapping) window.
# ===========================================================================

WB = (datetime(2026, 6, 15, 10, 0), datetime(2026, 6, 15, 14, 0))  # overlaps W1 (10:00-12:00 shared)


def test_buyfam_only_one_equal_member_clears_and_the_higher_welfare_one_wins():
    """Two 10MW buyers in one family, a seller for each product. Only one
    family member can clear. DCH: 10 x 100 - 10 x 5 = 950; DCL: 10 x 80 -
    10 x 5 = 750. So the DCH member clears, the DCL member and its seller
    do not. (The older test only asserted 'sum <= 1', which a result that
    rejects everything also satisfies.)"""
    sells = [_sell("SH", "U1", "B1", "PARENT", "DCH", 10, 5.0), _sell("SL", "U2", "B2", "PARENT", "DCL", 10, 5.0)]
    buys = [_buy("A", "DCH", 10, 100.0, family="F"), _buy("B", "DCL", 10, 80.0, family="F")]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id == {"A": 1.0, "B": 0.0}
    assert r.acceptance_ratio_by_order_id == {"SH": 1.0, "SL": 0.0}
    assert r.welfare == pytest.approx(950.0)


# Appendix B.1 (p.31). Buyer book: order 1 = 600MW P1 @10; order 2 = 200MW P1
# @5 (family 1); order 3 = 100MW P2 @12; order 4 = 100MW P2 @8; order 5 =
# 200MW P2 @5 (family 1). The report lists four possible outcomes "depending
# on available sell orders"; each is reproduced here by supplying exactly the
# sell volume that forces it (non-curtailable parents, limit 4 -- below every
# buyer, so no paradoxical acceptance is involved).
_B1_OUTCOMES = [
    # (P1 supply MW, P2 supply MW, expected ratios for orders 1..5, report's description)
    (600, 400, [1.0, 0.00, 1.0, 1.0, 1.00], "600 P1 / 400 P2: 0% of order 2, 100% of order 5"),
    (650, 350, [1.0, 0.25, 1.0, 1.0, 0.75], "650 P1 / 350 P2: 25% of order 2, 75% of order 5"),
    (800, 200, [1.0, 1.00, 1.0, 1.0, 0.00], "800 P1 / 200 P2: 100% of order 2, 0% of order 5"),
    (800, 100, [1.0, 1.00, 1.0, 0.0, 0.00], "800 P1 / 100 P2: order 4 rejected under scarcity"),
]


@pytest.mark.parametrize("p1_supply,p2_supply,expected,description", _B1_OUTCOMES, ids=[o[3][:12] for o in _B1_OUTCOMES])
def test_buyfam_report_appendix_b1_outcomes(p1_supply, p2_supply, expected, description):
    sells = [
        _sell("SP1", "U1", "B1", "PARENT", "P1", p1_supply, 4.0),
        _sell("SP2", "U2", "B2", "PARENT", "P2", p2_supply, 4.0),
    ]
    buys = [
        _buy("1", "P1", 600, 10.0), _buy("2", "P1", 200, 5.0, family="1"),
        _buy("3", "P2", 100, 12.0), _buy("4", "P2", 100, 8.0), _buy("5", "P2", 200, 5.0, family="1"),
    ]
    r = clear_auction(sells, buys)
    got = [r.buy_acceptance_ratio_by_order_id[str(i)] for i in range(1, 6)]
    assert got == pytest.approx(expected, abs=1e-4), description
    assert r.buy_acceptance_ratio_by_order_id["2"] + r.buy_acceptance_ratio_by_order_id["5"] <= 1.0 + 1e-6


def test_buyfam_constraint_is_on_ratios_so_unequal_volumes_split_by_ratio_not_mw():
    """Report B.2 (p.32): "two orders belonging to the same family may
    have different volumes". Family F: A = 10MW DCH, B = 20MW DCL, both
    at 10. Sellers: 5MW DCH and 10MW DCL at 3. Both clear at ratio 0.5 each
    (sum 1.0) -- 15MW in total, which a MW-based cap could not express.
    Welfare 5 x 10 + 10 x 10 - 15 x 3 = 105."""
    sells = [_sell("SH", "U1", "B1", "PARENT", "DCH", 5, 3.0), _sell("SL", "U2", "B2", "PARENT", "DCL", 10, 3.0)]
    buys = [_buy("A", "DCH", 10, 10.0, family="F"), _buy("B", "DCL", 20, 10.0, family="F")]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id == pytest.approx({"A": 0.5, "B": 0.5}, abs=1e-4)
    assert r.acceptance_ratio_by_order_id == {"SH": 1.0, "SL": 1.0}
    assert r.welfare == pytest.approx(105.0)


def test_buyfam_unequal_volumes_full_acceptance_of_both_members_is_impossible():
    """Same family, but sellers now cover BOTH members in full (10MW DCH,
    20MW DCL). That would need ratios 1 + 1 = 2 > 1, so only the more
    valuable member clears: DCL 20 x 10 - 20 x 3 = 140 beats DCH 10 x 10
    - 10 x 3 = 70."""
    sells = [_sell("SH", "U1", "B1", "PARENT", "DCH", 10, 3.0), _sell("SL", "U2", "B2", "PARENT", "DCL", 20, 3.0)]
    buys = [_buy("A", "DCH", 10, 10.0, family="F"), _buy("B", "DCL", 20, 10.0, family="F")]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id == {"A": 0.0, "B": 1.0}
    assert r.welfare == pytest.approx(140.0)


def test_buyfam_separate_families_are_independent_of_each_other():
    """Report B.2: two families constrain separately. F1 = {A (DCH, 100),
    B (DCL, 90)}, F2 = {C (DCH, 80), D (DCL, 70)}, 10MW each. A 20MW DCH
    seller needs A and C in full -- legal, one member from each family.
    A 20MW DCL seller would need B and D (also one each) but the market
    can only run one of the two sellers' products without exceeding a
    family. DCH: 1000 + 800 - 20 x 5 = 1700 beats DCL: 900 + 700 - 20 x 5 = 1500."""
    sells = [_sell("SH", "U1", "B1", "PARENT", "DCH", 20, 5.0), _sell("SL", "U2", "B2", "PARENT", "DCL", 20, 5.0)]
    buys = [
        _buy("A", "DCH", 10, 100.0, family="F1"), _buy("B", "DCL", 10, 90.0, family="F1"),
        _buy("C", "DCH", 10, 80.0, family="F2"), _buy("D", "DCL", 10, 70.0, family="F2"),
    ]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id == {"A": 1.0, "B": 0.0, "C": 1.0, "D": 0.0}
    assert r.welfare == pytest.approx(1700.0)


def test_buyfam_family_couples_members_across_overlapping_windows():
    """Report B.3 (p.32): a family can span concomitant service windows.
    A is in W1 (DCL, 100), B in WB (DCH, 60) -- windows share 10:00-12:00 --
    with one 10MW seller each (different units, so no basket exclusivity).
    Only one member clears: A, welfare 10 x 100 - 10 x 5 = 950; the WB
    seller is rejected because its buyer would break the family."""
    sells = [
        _sell("S1", "U1", "B1", "PARENT", "DCL", 10, 5.0, window=W1),
        _sell("S2", "U2", "B2", "PARENT", "DCH", 10, 5.0, window=WB),
    ]
    buys = [_buy("A", "DCL", 10, 100.0, window=W1, family="F"), _buy("B", "DCH", 10, 60.0, window=WB, family="F")]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id == {"A": 1.0, "B": 0.0}
    assert r.acceptance_ratio_by_order_id == {"S1": 1.0, "S2": 0.0}
    assert r.welfare == pytest.approx(950.0)
