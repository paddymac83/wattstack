"""Cost Minimisation Pricing, non-negative-surplus enforcement (rules 7
and 8) and the restricted/permissive overholding modes (Section 5.3).

Every expected number below was worked out by hand from the design
document's rules BEFORE running the solver; the arithmetic is shown in
each test's docstring so a failure can be traced to a rule rather than
to a number. No network calls anywhere.

Prices are £/MW/h, surpluses £/h = (price - limit) x MW x ratio.
"""
import dataclasses
import random
from datetime import datetime

import pytest

from wattstack_ingestion.eac_auction import (
    BuyOrderInput,
    ClearingResult,
    OverholdingMode,
    PricingInfeasibleError,
    SellOrderInput,
    backtest_hypothetical_bid,
    clear_auction,
    overholding_buy_order,
    verify_clearing,
)

W1 = (datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0))
W2 = (datetime(2026, 6, 15, 16, 0), datetime(2026, 6, 15, 20, 0))  # non-overlapping with W1
K1 = ("DCL", W1[0], W1[1])
K2 = ("DCL", W2[0], W2[1])

RESTRICTED = OverholdingMode.RESTRICTED
PERMISSIVE = OverholdingMode.PERMISSIVE


def _sell(order_id, unit, basket, order_type, quantity, price, product="DCL", window=W1, looped=None):
    return SellOrderInput(
        order_id=order_id, auction_unit=unit, basket_id=basket, order_type=order_type,
        auction_product=product, quantity_mw=quantity, price_limit=price,
        window_start=window[0], window_end=window[1], looped_basket_id=looped,
    )


def _parent(order_id, unit, quantity, price, **kw):
    """A standalone one-order basket named after its order."""
    return _sell(order_id, unit, order_id, "PARENT", quantity, price, **kw)


def _buy(order_id, quantity, price, product="DCL", window=W1, family=None, paradox_ok=True):
    return BuyOrderInput(
        order_id=order_id, auction_product=product, quantity_mw=quantity, price=price,
        window_start=window[0], window_end=window[1], substitutability_family=family,
        paradoxical_acceptance_allowed=paradox_ok,
    )


def _price(result, key=K1):
    return result.clearing_price_by_product_window[key]


# ===========================================================================
# Cost Minimisation Pricing (Section 7, 7.1.1, 7.2)
# ===========================================================================


def test_price_is_lowest_feasible_price_not_a_buy_price():
    """One parent, limit 5, MW 10, a 10MW buyer at 100. The lowest price
    keeping the parent's surplus >= 0 is its own limit: 5. Procurement
    cost = 10 x 5 = 50."""
    r = clear_auction([_parent("S", "U", 10, 5.0)], [_buy("B", 10, 100.0)])
    assert r.acceptance_ratio_by_order_id["S"] == 1.0
    assert _price(r) == pytest.approx(5.0)
    assert r.procurement_cost == pytest.approx(50.0)
    assert r.welfare == pytest.approx(1000.0 - 50.0)


@pytest.mark.parametrize("buy_price", [6.0, 20.0, 100.0])
def test_buy_limit_prices_do_not_move_the_clearing_price(buy_price):
    """Section 7.1.2: buy limit prices have no impact on the price."""
    r = clear_auction([_parent("S", "U", 10, 5.0)], [_buy("B", 10, buy_price)])
    assert _price(r) == pytest.approx(5.0)


def test_uniform_price_is_the_highest_accepted_standalone_limit():
    """Two standalone parents (limits 3 and 6), 20MW demand at 100.
    Both must have surplus >= 0 at ONE price, so the price is 6.
    Surpluses: (6-3) x 10 = 30 and (6-6) x 10 = 0. Cost 20 x 6 = 120."""
    r = clear_auction(
        [_parent("S1", "A", 10, 3.0), _parent("S2", "B", 10, 6.0)], [_buy("B1", 20, 100.0)]
    )
    assert _price(r) == pytest.approx(6.0)
    assert r.individual_surplus_by_order_id["S1"] == pytest.approx(30.0)
    assert r.individual_surplus_by_order_id["S2"] == pytest.approx(0.0, abs=1e-6)
    assert r.procurement_cost == pytest.approx(120.0)


def test_child_surplus_covers_a_loss_making_parent_rule_8():
    """Parent limit 10 (10MW), child limit 2 (10MW), buyer 20MW at 100.
    Basket total at price p: 10(p-10) + 10(p-2) >= 0  =>  p >= 6.
    At p = 6 the parent alone has surplus -40 (paradoxically accepted on
    its own) and the child +40: the basket nets to exactly 0. The old
    'max accepted limit' approximation would have said 10."""
    r = clear_auction(
        [_sell("P", "U", "B", "PARENT", 10, 10.0), _sell("C", "U", "B", "CHILD", 10, 2.0)],
        [_buy("D", 20, 100.0)],
    )
    assert r.acceptance_ratio_by_order_id == {"P": 1.0, "C": 1.0}
    assert _price(r) == pytest.approx(6.0)
    assert r.individual_surplus_by_order_id["P"] == pytest.approx(-40.0)
    assert r.individual_surplus_by_order_id["C"] == pytest.approx(40.0)
    assert r.basket_surplus_by_basket_id["B"] == pytest.approx(0.0, abs=1e-6)
    assert r.procurement_cost == pytest.approx(20 * 6.0)


def test_rule_7_child_limit_above_parent_limit_sets_the_price():
    """NOTE on what protects rule 7: it is enforced in Stage 2 (this
    test: the price is lifted to the child's limit) and re-checked by
    verify_clearing (test_verify_catches_a_child_priced_below_its_limit
    _rule_7). Its Stage 1 copy could NOT be shown to matter: deleting it
    changed neither welfare nor any outcome on 600 random baskets,
    because accepting a child at a price ceiling below its limit is
    strictly welfare-reducing (shrinking it together with the cheap
    buyer that caps the price always improves welfare). It stays in the
    formulation for faithfulness to Section 6.2, not because a test needs it.

    Parent limit 1, child limit 8. Rule 7 (child surplus >= 0) forces
    p >= 8 even though the basket total would be fine at a lower price:
    10(p-1) + 10(p-8) >= 0 only needs p >= 4.5. Price = 8; child surplus
    0; parent (8-1) x 10 = 70."""
    r = clear_auction(
        [_sell("P", "U", "B", "PARENT", 10, 1.0), _sell("C", "U", "B", "CHILD", 10, 8.0)],
        [_buy("D", 20, 100.0)],
    )
    assert _price(r) == pytest.approx(8.0)
    assert r.individual_surplus_by_order_id["C"] == pytest.approx(0.0, abs=1e-6)
    assert r.individual_surplus_by_order_id["P"] == pytest.approx(70.0)


def test_partially_accepted_child_price_uses_accepted_volume_only():
    """Parent limit 10 (10MW), child limit 2 (10MW), a single 15MW
    buyer at 100: the child clears at ratio 0.5 (5MW). Basket total:
    10(p-10) + 5(p-2) = 15p - 110 >= 0  =>  p = 110/15 = 7.3333."""
    r = clear_auction(
        [_sell("P", "U", "B", "PARENT", 10, 10.0), _sell("C", "U", "B", "CHILD", 10, 2.0)],
        [_buy("D", 15, 100.0)],
    )
    assert r.acceptance_ratio_by_order_id["P"] == 1.0
    assert r.acceptance_ratio_by_order_id["C"] == pytest.approx(0.5)
    assert _price(r) == pytest.approx(110 / 15, abs=1e-5)
    assert r.basket_surplus_by_basket_id["B"] == pytest.approx(0.0, abs=1e-4)


def test_parent_alone_must_cover_itself_when_its_children_are_rejected():
    """Same basket, but demand is only 10MW so the child is not used. A
    lone accepted parent needs surplus >= 0 on its own: price = 10."""
    r = clear_auction(
        [_sell("P", "U", "B", "PARENT", 10, 10.0), _sell("C", "U", "B", "CHILD", 10, 2.0)],
        [_buy("D", 10, 100.0)],
    )
    assert r.acceptance_ratio_by_order_id["C"] == 0.0
    assert _price(r) == pytest.approx(10.0)


def test_substitutable_children_only_the_cheaper_one_is_used_and_prices_the_basket():
    """Parent limit 4 (10MW), substitutable children A (limit 2) and B
    (limit 9), 10MW each, sum of ratios <= 1. 20MW demand => the cheaper
    child A is used in full. Basket total 10(p-4) + 10(p-2) >= 0 => p >= 3;
    rule 7: p >= 2. Price 3."""
    r = clear_auction(
        [
            _sell("P", "U", "B", "PARENT", 10, 4.0),
            _sell("A", "U", "B", "SUBSTITUTABLECHILD", 10, 2.0),
            _sell("Bc", "U", "B", "SUBSTITUTABLECHILD", 10, 9.0),
        ],
        [_buy("D", 20, 100.0)],
    )
    assert r.acceptance_ratio_by_order_id == {"P": 1.0, "A": 1.0, "Bc": 0.0}
    assert _price(r) == pytest.approx(3.0)


def test_looped_family_total_surplus_decides_the_procurement_cost():
    """Looped baskets B1 (W1, limit 10, 10MW) and B2 (W2, limit 2,
    10MW), 10MW buyer at 100 in each window. Rule 8 applies to the WHOLE
    family: 10(p1-10) + 10(p2-2) >= 0, i.e. p1 + p2 >= 12. Cost
    minimisation: 10 p1 + 10 p2 = 120 whatever the split (Section 7.2
    itself notes the split is not unique), so cost and family surplus are
    the determinate quantities."""
    r = clear_auction(
        [
            _parent("S1", "U", 10, 10.0, window=W1, looped="S2"),
            _parent("S2", "U", 10, 2.0, window=W2, looped="S1"),
        ],
        [_buy("D1", 10, 100.0, window=W1), _buy("D2", 10, 100.0, window=W2)],
    )
    assert r.acceptance_ratio_by_order_id == {"S1": 1.0, "S2": 1.0}
    assert r.procurement_cost == pytest.approx(120.0)
    assert r.looped_family_surplus[("S1", "S2")] == pytest.approx(0.0, abs=1e-6)
    assert sum(r.individual_surplus_by_order_id.values()) == pytest.approx(0.0, abs=1e-6)
    assert verify_clearing(
        [
            _parent("S1", "U", 10, 10.0, window=W1, looped="S2"),
            _parent("S2", "U", 10, 2.0, window=W2, looped="S1"),
        ],
        [_buy("D1", 10, 100.0, window=W1), _buy("D2", 10, 100.0, window=W2)],
        r,
    ) == []


def test_window_with_no_accepted_volume_reports_zero_price():
    r = clear_auction([_parent("S", "U", 10, 50.0)], [_buy("B", 10, 5.0)])
    assert r.acceptance_ratio_by_order_id["S"] == 0.0
    assert _price(r) == 0.0
    assert r.procurement_cost == 0.0


def test_price_is_independent_per_window():
    r = clear_auction(
        [_parent("S1", "A", 10, 4.0, window=W1), _parent("S2", "A", 10, 9.0, window=W2)],
        [_buy("D1", 10, 100.0, window=W1), _buy("D2", 10, 100.0, window=W2)],
    )
    assert _price(r, K1) == pytest.approx(4.0)
    assert _price(r, K2) == pytest.approx(9.0)


# ===========================================================================
# Rules 7/8 interacting with buyers that forbid paradoxical acceptance
# (these are the cases where acceptance itself changes, not just the price)
# ===========================================================================


def test_lone_parent_rejected_when_a_no_paradox_buyer_would_cap_the_price_below_its_limit():
    """Parent limit 5 (10MW). Demand: 5MW at 100 and 5MW at 2 where the
    2-buyer FORBIDS paradoxical acceptance. Welfare of accepting is
    500 + 10 - 50 = +460, but volume balance needs BOTH buyers, so the
    price must be <= 2 while the parent needs >= 5: no price exists.
    The parent must be rejected."""
    r = clear_auction(
        [_parent("P", "U", 10, 5.0)],
        [_buy("hi", 5, 100.0), _buy("lo", 5, 2.0, paradox_ok=False)],
    )
    assert r.acceptance_ratio_by_order_id["P"] == 0.0
    assert r.welfare == 0.0


def test_same_lone_parent_accepted_when_the_low_buyer_may_be_paradoxically_accepted():
    """As above but the 2-buyer allows paradoxical acceptance (the
    document's default): price 5, the 2-buyer is the single paradoxically
    accepted order, which RESTRICTED mode permits."""
    r = clear_auction(
        [_parent("P", "U", 10, 5.0)],
        [_buy("hi", 5, 100.0), _buy("lo", 5, 2.0)],
    )
    assert r.acceptance_ratio_by_order_id["P"] == 1.0
    assert _price(r) == pytest.approx(5.0)
    assert r.paradoxically_accepted_buy_order_ids == ["lo"]
    assert r.welfare == pytest.approx(500 + 10 - 50)


def test_rule_8_stops_a_child_subsidy_that_no_permitted_price_can_support():
    """Parent limit 7, child limit 2 (10MW each); buyers 10MW at 100 and
    10MW at 3, BOTH forbidding paradox. Using the child needs the 3-buyer
    (volume balance), capping the price at 3; the basket needs
    10(p-7) + 10x(p-2) >= 0 which at p = 3 needs x >= 4 (> 1). So the
    child is unusable: parent alone + the 100 buyer, price 7, welfare
    1000 - 70 = 930. (An acceptance-only model would take both for
    1000 + 30 - 70 - 20 = 940.)"""
    r = clear_auction(
        [_sell("P", "U", "B", "PARENT", 10, 7.0), _sell("C", "U", "B", "CHILD", 10, 2.0)],
        [_buy("hi", 10, 100.0, paradox_ok=False), _buy("lo", 10, 3.0, paradox_ok=False)],
    )
    assert r.acceptance_ratio_by_order_id == {"P": 1.0, "C": 0.0}
    assert r.buy_acceptance_ratio_by_order_id == {"hi": 1.0, "lo": 0.0}
    assert _price(r) == pytest.approx(7.0)
    assert r.welfare == pytest.approx(930.0)


def test_rule_8_child_subsidy_used_once_the_low_buyer_may_be_paradoxical():
    """Same market, buyers allow paradox. Both accepted: 1000 + 30 - 70 -
    20 = 940. Basket total 10(p-7) + 10(p-2) >= 0 => p = 4.5; the 3-buyer
    is paradoxical (4.5 > 3) and is the only one, so RESTRICTED allows it."""
    r = clear_auction(
        [_sell("P", "U", "B", "PARENT", 10, 7.0), _sell("C", "U", "B", "CHILD", 10, 2.0)],
        [_buy("hi", 10, 100.0), _buy("lo", 10, 3.0)],
    )
    assert r.acceptance_ratio_by_order_id == {"P": 1.0, "C": 1.0}
    assert _price(r) == pytest.approx(4.5)
    assert r.paradoxically_accepted_buy_order_ids == ["lo"]
    assert r.welfare == pytest.approx(940.0)


def _loop_with_low_buyers(paradox_ok):
    sells = [
        _parent("S1", "U", 10, 10.0, window=W1, looped="S2"),
        _parent("S2", "U", 10, 2.0, window=W2, looped="S1"),
    ]
    buys = [
        _buy("w1_hi", 5, 100.0, window=W1),
        _buy("w1_lo", 5, 3.0, window=W1, paradox_ok=paradox_ok),
        _buy("w2_lo", 10, 3.0, window=W2, paradox_ok=paradox_ok),
    ]
    return sells, buys


def test_looped_family_rejected_when_no_permitted_prices_cover_the_family_total():
    """Loop needs p1 + p2 >= 12 (10(p1-10)+10(p2-2) >= 0). Volume balance
    forces every buyer in, so with the 3-buyers forbidding paradox both
    prices are capped at 3: the most the family can earn is 60 against a
    requirement of 120. Welfare of the loop would be +425 but it has no
    valid price, so BOTH baskets are rejected (rule 5 + rule 8)."""
    sells, buys = _loop_with_low_buyers(paradox_ok=False)
    r = clear_auction(sells, buys)
    assert r.acceptance_ratio_by_order_id == {"S1": 0.0, "S2": 0.0}
    assert r.welfare == 0.0


def test_looped_family_accepted_when_the_low_buyers_may_be_paradoxical():
    """Same loop, paradox allowed. Welfare W1: 500 + 15 - 100 = 415; W2:
    30 - 20 = 10; total 425. Cost 120.

    WHICH low buyer is paradoxical is NOT determinate: p1 + p2 = 12 can
    be split many ways at identical cost (Section 7.2's residual price
    indeterminacy), and w2_lo (limit 3) is paradoxical only if p2 > 3.
    What is determinate: at most one paradoxical buyer per window (the
    RESTRICTED cap), and at least one overall -- if neither were
    paradoxical both prices would be capped at 3 and p1 + p2 <= 6 < 12."""
    sells, buys = _loop_with_low_buyers(paradox_ok=True)
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.acceptance_ratio_by_order_id == {"S1": 1.0, "S2": 1.0}
    assert r.welfare == pytest.approx(425.0)
    assert r.procurement_cost == pytest.approx(120.0)
    paradoxical = set(r.paradoxically_accepted_buy_order_ids)
    assert paradoxical and paradoxical <= {"w1_lo", "w2_lo"}
    assert verify_clearing(sells, buys, r) == []


# ===========================================================================
# Overholding: restricted (Section 5.3 item 1) vs permissive (item 2)
# ===========================================================================


def _lumpy_30(paradox_ok=True):
    sells = [_parent("S", "U", 30, 3.0)]
    buys = [
        _buy("B1", 10, 10.0),
        _buy("B2", 10, 2.0, paradox_ok=paradox_ok),
        _buy("B3", 10, 1.0, paradox_ok=paradox_ok),
    ]
    return sells, buys


def test_default_mode_is_restricted():
    sells, buys = _lumpy_30()
    assert clear_auction(sells, buys).overholding_mode is RESTRICTED


def test_restricted_mode_rejects_a_trade_needing_two_paradoxical_buy_orders():
    """30MW non-curtailable at limit 3. Buyers 10@10, 10@2, 10@1. At
    price 3 the 2- and 1-buyers are both paradoxical. Restricted: at
    most one, so at most 10 + 10 = 20MW of demand < 30MW: no trade."""
    sells, buys = _lumpy_30()
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.acceptance_ratio_by_order_id["S"] == 0.0
    assert r.welfare == 0.0
    assert r.paradoxically_accepted_buy_order_ids == []


def test_permissive_mode_accepts_the_same_trade_with_two_paradoxical_buy_orders():
    """Same market, permissive: welfare 100 + 20 + 10 - 90 = +40, price
    3, paradoxical orders B2 and B3 with 20MW over-procured."""
    sells, buys = _lumpy_30()
    r = clear_auction(sells, buys, overholding_mode=PERMISSIVE)
    assert r.acceptance_ratio_by_order_id["S"] == 1.0
    assert r.welfare == pytest.approx(40.0)
    assert _price(r) == pytest.approx(3.0)
    assert r.paradoxically_accepted_buy_order_ids == ["B2", "B3"]
    assert r.paradoxical_buy_volume_mw_by_product_window[K1] == pytest.approx(20.0)


def test_permissive_mode_still_respects_the_buyer_paradox_flag():
    """With B2/B3 forbidding paradoxical acceptance even PERMISSIVE
    cannot use them: only B1's 10MW is available for a 30MW order."""
    sells, buys = _lumpy_30(paradox_ok=False)
    r = clear_auction(sells, buys, overholding_mode=PERMISSIVE)
    assert r.acceptance_ratio_by_order_id["S"] == 0.0


def test_restricted_mode_allows_exactly_one_paradoxical_buy_order():
    """20MW at limit 3; buyers 10@10 and 10@2. One paradoxical buyer:
    allowed. Welfare 100 + 20 - 60 = 60, both modes."""
    sells = [_parent("S", "U", 20, 3.0)]
    buys = [_buy("B1", 10, 10.0), _buy("B2", 10, 2.0)]
    for mode in (RESTRICTED, PERMISSIVE):
        r = clear_auction(sells, buys, overholding_mode=mode)
        assert r.acceptance_ratio_by_order_id["S"] == 1.0
        assert r.welfare == pytest.approx(60.0)
        assert r.paradoxically_accepted_buy_order_ids == ["B2"]


def test_two_overholding_orders_distinguish_item_1_from_item_2():
    """The document's own distinction, with real overholding orders.
    30MW at limit 3; real demand 10@10; two 10MW zero-price overholding
    orders. Price 3 > 0 so BOTH overholding orders are paradoxical.
    Item 1 (restricted): rejected. Item 2 (permissive): accepted,
    welfare 100 - 90 = +10, 20MW over-procured."""
    sells = [_parent("S", "U", 30, 3.0)]
    buys = [
        _buy("D", 10, 10.0),
        overholding_buy_order("OH1", "DCL", 10, W1[0], W1[1]),
        overholding_buy_order("OH2", "DCL", 10, W1[0], W1[1]),
    ]
    restricted = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    permissive = clear_auction(sells, buys, overholding_mode=PERMISSIVE)
    assert restricted.acceptance_ratio_by_order_id["S"] == 0.0
    assert permissive.acceptance_ratio_by_order_id["S"] == 1.0
    assert permissive.welfare == pytest.approx(10.0)
    assert permissive.paradoxically_accepted_buy_order_ids == ["OH1", "OH2"]
    assert permissive.paradoxical_buy_volume_mw_by_product_window[K1] == pytest.approx(20.0)


def test_one_large_overholding_order_is_item_1s_intended_form():
    """The document's restricted form: ONE overholding order whose
    volume is the maximum over-procurement (here 20MW). Same trade as
    above is accepted in restricted mode, because only one buy order is
    paradoxical."""
    sells = [_parent("S", "U", 30, 3.0)]
    buys = [_buy("D", 10, 10.0), overholding_buy_order("OH", "DCL", 20, W1[0], W1[1])]
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.acceptance_ratio_by_order_id["S"] == 1.0
    assert r.paradoxically_accepted_buy_order_ids == ["OH"]
    assert r.welfare == pytest.approx(10.0)


def test_zero_priced_overholding_order_is_not_paradoxical_at_a_negative_price():
    """20MW at limit -5 (the document allows negative prices), demand
    10@10 plus two 5MW overholding orders at 0. The price settles at -5,
    BELOW the overholding orders' limit of 0, so accepting them is not
    paradoxical and RESTRICTED mode does not count them. Welfare
    100 + 0 + 100 = 200."""
    sells = [_parent("S", "U", 20, -5.0)]
    buys = [
        _buy("D", 10, 10.0),
        overholding_buy_order("OH1", "DCL", 5, W1[0], W1[1]),
        overholding_buy_order("OH2", "DCL", 5, W1[0], W1[1]),
    ]
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.acceptance_ratio_by_order_id["S"] == 1.0
    assert _price(r) == pytest.approx(-5.0)
    assert r.paradoxically_accepted_buy_order_ids == []
    assert r.welfare == pytest.approx(200.0)


def test_restricted_cap_applies_per_product_and_window_not_globally():
    """Two windows, each needing one paradoxical buyer: fine, because
    the cap is per (product, window)."""
    sells = [
        _parent("S1", "A", 20, 3.0, window=W1),
        _parent("S2", "B", 20, 3.0, window=W2),
    ]
    buys = [
        _buy("a_hi", 10, 10.0, window=W1), _buy("a_lo", 10, 2.0, window=W1),
        _buy("b_hi", 10, 10.0, window=W2), _buy("b_lo", 10, 2.0, window=W2),
    ]
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.acceptance_ratio_by_order_id == {"S1": 1.0, "S2": 1.0}
    assert sorted(r.paradoxically_accepted_buy_order_ids) == ["a_lo", "b_lo"]


def test_restricted_cap_applies_per_product():
    """Two products in the same window clear independently: one
    paradoxical buyer in each is within the cap."""
    sells = [
        _parent("SL", "A", 20, 3.0, product="DCL"),
        _parent("SH", "B", 20, 3.0, product="DCH"),
    ]
    buys = [
        _buy("l_hi", 10, 10.0, product="DCL"), _buy("l_lo", 10, 2.0, product="DCL"),
        _buy("h_hi", 10, 10.0, product="DCH"), _buy("h_lo", 10, 2.0, product="DCH"),
    ]
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.acceptance_ratio_by_order_id == {"SL": 1.0, "SH": 1.0}


def test_modes_agree_when_no_paradox_arises():
    sells = [_parent("S1", "A", 10, 3.0), _parent("S2", "B", 10, 6.0)]
    buys = [_buy("B1", 20, 100.0)]
    a = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    b = clear_auction(sells, buys, overholding_mode=PERMISSIVE)
    assert a.acceptance_ratio_by_order_id == b.acceptance_ratio_by_order_id
    assert a.clearing_price_by_product_window == b.clearing_price_by_product_window
    assert a.welfare == pytest.approx(b.welfare)


def test_overholding_mode_accepts_its_string_value_and_rejects_nonsense():
    sells, buys = _lumpy_30()
    assert clear_auction(sells, buys, overholding_mode="permissive").overholding_mode is PERMISSIVE
    with pytest.raises(ValueError):
        clear_auction(sells, buys, overholding_mode="very-permissive")


# ===========================================================================
# Price bounds (Section 7.3)
# ===========================================================================


def test_default_price_bounds_are_derived_from_the_orders():
    r = clear_auction([_parent("S", "U", 10, 5.0)], [_buy("B", 10, 100.0)])
    assert r.price_bounds == (0.0, 100.0)
    r2 = clear_auction([_parent("S", "U", 10, -3.0)], [_buy("B", 10, 8.0)])
    assert r2.price_bounds == (-3.0, 8.0)


def test_explicit_price_bounds_are_recorded_and_respected():
    r = clear_auction([_parent("S", "U", 10, 5.0)], [_buy("B", 10, 100.0)], price_bounds=(0.0, 200.0))
    assert r.price_bounds == (0.0, 200.0)
    assert _price(r) == pytest.approx(5.0)


def test_explicit_negative_minimum_bound_is_allowed():
    r = clear_auction([_parent("S", "U", 10, -3.0)], [_buy("B", 10, 8.0)], price_bounds=(-10.0, 10.0))
    assert _price(r) == pytest.approx(-3.0)


def test_order_prices_outside_explicit_bounds_are_rejected():
    with pytest.raises(ValueError, match="outside price_bounds"):
        clear_auction([_parent("S", "U", 10, 50.0)], [_buy("B", 10, 100.0)], price_bounds=(0.0, 20.0))


def test_inverted_bounds_are_rejected():
    with pytest.raises(ValueError, match="exceeds maximum"):
        clear_auction([_parent("S", "U", 10, 5.0)], [_buy("B", 10, 6.0)], price_bounds=(10.0, 0.0))


# ===========================================================================
# Input validation (fail loudly rather than clear a partial basket)
# ===========================================================================


def test_duplicate_sell_order_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate sell"):
        clear_auction([_parent("S", "A", 10, 1.0), _parent("S", "B", 10, 1.0)], [])


def test_duplicate_buy_order_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate buy"):
        clear_auction([], [_buy("B", 10, 1.0), _buy("B", 10, 2.0)])


def test_unknown_order_type_is_rejected():
    with pytest.raises(ValueError, match="unknown order_type"):
        clear_auction([_sell("S", "A", "B", "ORPHAN", 10, 1.0)], [])


def test_child_without_a_parent_is_rejected_not_silently_accepted():
    """A truncated fetch can drop a parent row. A free-floating child
    must not be accepted (rule 4) and its basket surplus is undefined."""
    with pytest.raises(ValueError, match="PARENT"):
        clear_auction([_sell("C", "A", "B", "CHILD", 10, 1.0)], [_buy("D", 10, 5.0)])


def test_basket_with_two_parents_is_rejected():
    with pytest.raises(ValueError, match="PARENT"):
        clear_auction(
            [_sell("P1", "A", "B", "PARENT", 10, 1.0), _sell("P2", "A", "B", "PARENT", 10, 1.0)], []
        )


def test_empty_auction_clears_to_an_empty_result():
    r = clear_auction([], [])
    assert r.acceptance_ratio_by_order_id == {}
    assert r.clearing_price_by_product_window == {}
    assert r.welfare == 0.0


def test_sell_orders_without_any_buyers_are_all_rejected():
    r = clear_auction([_parent("S", "U", 10, 1.0)], [])
    assert r.acceptance_ratio_by_order_id["S"] == 0.0


# ===========================================================================
# Result bookkeeping
# ===========================================================================


def test_result_welfare_and_procurement_cost_are_consistent_with_its_own_parts():
    r = clear_auction(
        [_sell("P", "U", "B", "PARENT", 10, 10.0), _sell("C", "U", "B", "CHILD", 10, 2.0)],
        [_buy("D", 15, 100.0)],
    )
    sold = {"P": 10 * 1.0, "C": 10 * r.acceptance_ratio_by_order_id["C"]}
    limits = {"P": 10.0, "C": 2.0}
    assert r.welfare == pytest.approx(15 * 100.0 - sum(sold[k] * limits[k] for k in sold), abs=1e-3)
    assert r.procurement_cost == pytest.approx(sum(sold.values()) * _price(r), abs=1e-3)
    assert r.buy_acceptance_ratio_by_order_id == {"D": 1.0}


def test_basket_surplus_is_reported_only_for_accepted_baskets():
    r = clear_auction(
        [_parent("S1", "A", 10, 3.0), _parent("S2", "B", 10, 90.0)], [_buy("D", 10, 20.0)]
    )
    assert "S1" in r.basket_surplus_by_basket_id
    assert "S2" not in r.basket_surplus_by_basket_id


def test_input_order_does_not_change_the_outcome():
    sells = [
        _sell("P", "U", "B", "PARENT", 10, 10.0), _sell("C", "U", "B", "CHILD", 10, 2.0),
        _parent("X", "V", 5, 4.0),
    ]
    buys = [_buy("D", 20, 100.0), _buy("E", 5, 50.0)]
    a = clear_auction(sells, buys)
    b = clear_auction(list(reversed(sells)), list(reversed(buys)))
    assert a.acceptance_ratio_by_order_id == b.acceptance_ratio_by_order_id
    assert a.welfare == pytest.approx(b.welfare)
    assert _price(a) == pytest.approx(_price(b))


def test_clearing_result_new_fields_have_safe_defaults():
    r = ClearingResult(acceptance_ratio_by_order_id={}, clearing_price_by_product_window={})
    assert r.paradoxically_accepted_buy_order_ids == []
    assert r.overholding_mode is RESTRICTED
    assert r.price_bounds is None
    assert r.welfare == 0.0


# ===========================================================================
# verify_clearing: the independent rule checker must catch each rule
# ===========================================================================


def _basket_market():
    sells = [_sell("P", "U", "B", "PARENT", 10, 10.0), _sell("C", "U", "B", "CHILD", 10, 2.0)]
    buys = [_buy("D", 20, 100.0)]
    return sells, buys, clear_auction(sells, buys)


def _tamper(result, **changes):
    return dataclasses.replace(result, **changes)


def test_verify_accepts_a_genuine_result():
    sells, buys, r = _basket_market()
    assert verify_clearing(sells, buys, r) == []


def test_verify_catches_a_fractional_parent():
    sells, buys, r = _basket_market()
    bad = _tamper(r, acceptance_ratio_by_order_id={**r.acceptance_ratio_by_order_id, "P": 0.5})
    assert any("rule 1" in v for v in verify_clearing(sells, buys, bad))


def test_verify_catches_a_child_accepted_beyond_its_parent():
    sells, buys, r = _basket_market()
    bad = _tamper(r, acceptance_ratio_by_order_id={"P": 0.0, "C": 1.0})
    assert any("rule 4" in v for v in verify_clearing(sells, buys, bad))


def test_verify_catches_a_child_priced_below_its_limit_rule_7():
    sells = [_sell("P", "U", "B", "PARENT", 10, 1.0), _sell("C", "U", "B", "CHILD", 10, 8.0)]
    buys = [_buy("D", 20, 100.0)]
    r = clear_auction(sells, buys)
    bad = _tamper(r, clearing_price_by_product_window={K1: 5.0})
    violations = verify_clearing(sells, buys, bad)
    assert any("rule 7" in v for v in violations)
    assert not any("rule 8" in v for v in violations)  # 10(5-1) + 10(5-8) = +10, basket still fine


def test_verify_catches_a_basket_with_negative_total_surplus_rule_8():
    sells, buys, r = _basket_market()
    bad = _tamper(r, clearing_price_by_product_window={K1: 3.0})  # 10(3-10) + 10(3-2) = -60
    violations = verify_clearing(sells, buys, bad)
    assert any("rule 8" in v for v in violations)
    assert not any("rule 7" in v for v in violations)  # child alone: 3 >= 2


def test_verify_catches_unequal_looped_acceptance_rule_5():
    sells = [
        _parent("S1", "U", 10, 1.0, window=W1, looped="S2"),
        _parent("S2", "U", 10, 1.0, window=W2, looped="S1"),
    ]
    buys = [_buy("D1", 10, 9.0, window=W1), _buy("D2", 10, 9.0, window=W2)]
    r = clear_auction(sells, buys)
    bad = _tamper(r, acceptance_ratio_by_order_id={"S1": 1.0, "S2": 0.0})
    assert any("rule 5" in v for v in verify_clearing(sells, buys, bad))


def test_verify_catches_two_overlapping_baskets_of_one_unit_rule_6():
    sells = [_parent("S1", "U", 10, 1.0), _parent("S2", "U", 10, 1.0)]
    buys = [_buy("D", 20, 9.0)]
    r = clear_auction(sells, buys)
    assert sum(r.acceptance_ratio_by_order_id.values()) <= 1.0
    bad = _tamper(r, acceptance_ratio_by_order_id={"S1": 1.0, "S2": 1.0}, buy_acceptance_ratio_by_order_id={"D": 1.0})
    assert any("rule 6" in v for v in verify_clearing(sells, buys, bad))


def test_verify_catches_a_volume_imbalance():
    sells, buys, r = _basket_market()
    bad = _tamper(r, buy_acceptance_ratio_by_order_id={"D": 0.5})
    assert any("volume balance" in v for v in verify_clearing(sells, buys, bad))


def test_verify_catches_a_buy_family_over_one():
    sells = [_parent("S1", "A", 10, 1.0), _parent("S2", "B", 10, 1.0)]
    buys = [_buy("D1", 10, 9.0, family="F"), _buy("D2", 10, 9.0, family="F")]
    r = clear_auction(sells, buys)
    bad = _tamper(
        r, acceptance_ratio_by_order_id={"S1": 1.0, "S2": 1.0}, buy_acceptance_ratio_by_order_id={"D1": 1.0, "D2": 1.0}
    )
    assert any("buy family" in v for v in verify_clearing(sells, buys, bad))


def test_verify_catches_a_price_outside_the_bounds():
    sells, buys, r = _basket_market()
    bad = _tamper(r, clearing_price_by_product_window={K1: 1e6})
    assert any("outside bounds" in v for v in verify_clearing(sells, buys, bad))


def test_verify_applies_the_restricted_cap_but_not_the_permissive_one():
    sells, buys = _lumpy_30()
    permissive = clear_auction(sells, buys, overholding_mode=PERMISSIVE)
    assert verify_clearing(sells, buys, permissive, overholding_mode=PERMISSIVE) == []
    violations = verify_clearing(sells, buys, permissive, overholding_mode=RESTRICTED)
    assert any("restricted overholding" in v for v in violations)


def test_verify_catches_paradoxical_acceptance_the_buyer_forbids():
    sells = [_parent("P", "U", 10, 5.0)]
    allowed = [_buy("hi", 5, 100.0), _buy("lo", 5, 2.0)]
    r = clear_auction(sells, allowed)
    assert r.acceptance_ratio_by_order_id["P"] == 1.0
    forbidding = [_buy("hi", 5, 100.0), _buy("lo", 5, 2.0, paradox_ok=False)]
    assert any("forbids" in v for v in verify_clearing(sells, forbidding, r))


# ===========================================================================
# Independent brute-force oracle for the single-window, parents-only case.
# It re-derives the optimum by enumeration, sharing no code with the MILP,
# so an encoding mistake (a wrong level set, a missing paradox constraint)
# cannot hide behind the solver agreeing with itself.
# ===========================================================================


def _oracle_best_welfare(sells, buys, mode):
    """sells: [(qty, limit)] (each its own basket and unit); buys:
    [(qty, price, paradox_ok)]. For a set S of accepted sells the lowest
    price that satisfies every accepted sell is p = max(limit); a LOWER
    price never hurts the buy side, so p is optimal. Buys are then: free
    (price >= p), paradoxical-and-allowed (price < p), or unusable
    (price < p and paradox forbidden). Fill the volume from the highest
    prices, using at most one paradoxical buyer in restricted mode."""
    best = 0.0
    n = len(sells)
    for mask in range(1, 1 << n):
        chosen = [sells[i] for i in range(n) if mask >> i & 1]
        volume = sum(q for q, _ in chosen)
        cost = sum(q * c for q, c in chosen)
        price = max(c for _, c in chosen)
        free = [(p, q) for q, p, _ in buys if p >= price]
        paradoxical = [(p, q) for q, p, ok in buys if p < price and ok]
        if mode is RESTRICTED:
            options = [None] + list(range(len(paradoxical)))
        else:
            options = ["all"]
        for opt in options:
            available = list(free)
            if opt == "all":
                available += paradoxical
            elif opt is not None:
                available.append(paradoxical[opt])
            if sum(q for _, q in available) < volume - 1e-9:
                continue
            remaining, utility = volume, 0.0
            for p, q in sorted(available, reverse=True):
                take = min(q, remaining)
                utility += take * p
                remaining -= take
            best = max(best, utility - cost)
    return best


N_ORACLE_SEEDS = 80


def _random_market(seed):
    """Built to make the paradox cap bite: lumpy sells (20-40MW) with
    limits 3-5, one or two high-priced 'anchor' buyers (8-12) and
    several small low-priced buyers (0-5, many below the sell limit)
    whose combined volume is what a lumpy sell needs. A uniform random
    generator was tried first and never made the two modes disagree."""
    rng = random.Random(seed)
    sells = [(rng.choice([20, 25, 30, 40]), float(rng.randint(3, 5))) for _ in range(rng.randint(1, 2))]
    buys = [(rng.choice([10, 15]), float(rng.randint(8, 12)), rng.random() > 0.15) for _ in range(rng.randint(1, 2))]
    buys += [(5, float(rng.randint(0, 5)), rng.random() > 0.15) for _ in range(rng.randint(3, 5))]
    sell_orders = [_parent(f"S{i}", f"U{i}", q, c) for i, (q, c) in enumerate(sells)]
    buy_orders = [_buy(f"B{j}", q, p, paradox_ok=ok) for j, (q, p, ok) in enumerate(buys)]
    return sells, buys, sell_orders, buy_orders


@pytest.mark.parametrize("seed", range(N_ORACLE_SEEDS))
def test_milp_welfare_matches_brute_force_oracle_in_both_modes(seed):
    sells, buys, sell_orders, buy_orders = _random_market(seed)
    welfare = {}
    for mode in (RESTRICTED, PERMISSIVE):
        result = clear_auction(sell_orders, buy_orders, overholding_mode=mode)
        welfare[mode] = result.welfare
        assert result.welfare == pytest.approx(_oracle_best_welfare(sells, buys, mode), abs=1e-4), (
            f"seed {seed}, mode {mode.value}"
        )
        assert verify_clearing(sell_orders, buy_orders, result, overholding_mode=mode) == []
    assert welfare[PERMISSIVE] >= welfare[RESTRICTED] - 1e-6  # Section 5.3: "higher welfare"


def test_the_random_instances_actually_exercise_the_paradox_cap():
    """Guard against a vacuous oracle test: across the seeds above the
    two modes must disagree on welfare often enough that matching the
    oracle in BOTH modes really does distinguish them."""
    differing = 0
    for seed in range(N_ORACLE_SEEDS):
        sells, buys, _, _ = _random_market(seed)
        if abs(_oracle_best_welfare(sells, buys, PERMISSIVE) - _oracle_best_welfare(sells, buys, RESTRICTED)) > 1e-9:
            differing += 1
    assert differing >= 5


# ===========================================================================
# backtest_hypothetical_bid passes the new options through
# ===========================================================================


class _FakeProvider:
    def __init__(self, sells=(), buys=()):
        self._sells, self._buys = list(sells), list(buys)

    def sell_orders(self, windows, product):
        return [s for s in self._sells if s.auction_product == product]

    def buy_orders(self, windows, product):
        return [b for b in self._buys if b.auction_product == product]


def test_backtest_overholding_mode_changes_whether_my_bid_is_accepted():
    """My 30MW bid at 3 against buyers 10@10, 10@2, 10@1: needs two
    paradoxical buyers, so it is accepted only in PERMISSIVE mode."""
    provider = _FakeProvider(buys=_lumpy_30()[1])
    restricted = backtest_hypothetical_bid(
        provider, [W1], ["DCL"], "MINE", "DCL", my_price=3.0, my_quantity_mw=30, overholding_mode=RESTRICTED
    )
    permissive = backtest_hypothetical_bid(
        provider, [W1], ["DCL"], "MINE", "DCL", my_price=3.0, my_quantity_mw=30, overholding_mode=PERMISSIVE
    )
    assert not restricted.hypothetical_bid_accepted
    assert permissive.hypothetical_bid_accepted
    assert _price(permissive) == pytest.approx(3.0)


def test_backtest_default_mode_is_restricted():
    provider = _FakeProvider(buys=_lumpy_30()[1])
    r = backtest_hypothetical_bid(provider, [W1], ["DCL"], "MINE", "DCL", my_price=3.0, my_quantity_mw=30)
    assert r.overholding_mode is RESTRICTED


def test_backtest_passes_price_bounds_through():
    provider = _FakeProvider(buys=[_buy("B", 10, 100.0)])
    with pytest.raises(ValueError, match="outside price_bounds"):
        backtest_hypothetical_bid(
            provider, [W1], ["DCL"], "MINE", "DCL", my_price=5.0, my_quantity_mw=10, price_bounds=(0.0, 50.0)
        )


def test_backtest_acceptance_flips_as_my_price_crosses_the_competition():
    """The question this module exists for, in miniature: a competitor
    offers 10MW at 6; demand is 10MW at 100. My 10MW bid is accepted
    only while it undercuts the competitor, and the price it clears at
    is always the lowest feasible one -- my own limit while I am
    marginal."""
    competitor = [_parent("RIVAL", "RIVAL_UNIT", 10, 6.0)]
    provider = _FakeProvider(sells=competitor, buys=[_buy("D", 10, 100.0)])
    outcomes = {}
    for my_price in (4.0, 5.9, 6.1, 9.0):
        r = backtest_hypothetical_bid(provider, [W1], ["DCL"], "MINE", "DCL", my_price=my_price, my_quantity_mw=10)
        outcomes[my_price] = (r.hypothetical_bid_accepted, _price(r))
    assert outcomes[4.0] == (True, pytest.approx(4.0))
    assert outcomes[5.9] == (True, pytest.approx(5.9))
    assert outcomes[6.1][0] is False and outcomes[6.1][1] == pytest.approx(6.0)
    assert outcomes[9.0][0] is False


# ===========================================================================
# Regression: the restricted cap must be enforced on the ACTUAL prices
# ===========================================================================


def _coupled_loop_market():
    """Looped family S1 (W1, limit 10, 10MW) + S2 (W2, limit 2, 10MW),
    plus an unrelated seller S3 (W2, limit 1, 10MW) paid the same W2
    price. W1 has two low buyers that volume balance forces in (3 and
    2); W2 has a deep 20MW buyer at 100.

    Family rule: 10(p1 - 10) + 10(p2 - 2) >= 0, i.e. p1 + p2 >= 12.
    Raising p2 costs 20 per unit (S2 and S3 are both paid it), raising p1
    costs only 10 per unit, so cost minimisation WANTS p1 high."""
    sells = [
        _parent("S1", "U", 10, 10.0, window=W1, looped="S2"),
        _parent("S2", "U", 10, 2.0, window=W2, looped="S1"),
        _parent("S3", "V", 10, 1.0, window=W2),
    ]
    buys = [_buy("w1_x", 5, 3.0, window=W1), _buy("w1_y", 5, 2.0, window=W1), _buy("w2", 20, 100.0, window=W2)]
    return sells, buys


def test_permissive_mode_lets_cost_minimisation_push_the_price_into_the_cheap_window():
    """No cap: p2 >= 1 (S3's own limit), so p1 = 11. Cost 10 x 11 + 20 x 1 =
    130. Both W1 buyers end up paradoxical (11 > 3 and 11 > 2): allowed."""
    sells, buys = _coupled_loop_market()
    r = clear_auction(sells, buys, overholding_mode=PERMISSIVE)
    assert _price(r, K1) == pytest.approx(11.0)
    assert _price(r, K2) == pytest.approx(1.0)
    assert r.procurement_cost == pytest.approx(130.0)
    assert r.paradoxically_accepted_buy_order_ids == ["w1_x", "w1_y"]


def test_restricted_mode_enforces_the_cap_on_the_final_prices_not_just_in_stage_1():
    """Cap = 1 paradoxical buyer in W1 forces p1 <= 3 (above 3 both low
    buyers are paradoxical), so the family total must come from W2:
    p2 >= 9. Cost 10 x 3 + 20 x 9 = 210 (> 130, the price of the cap).
    Before the fix Stage 2 returned p1 = 11 here, two paradoxical buyers
    in one window, and the independent checker raised."""
    sells, buys = _coupled_loop_market()
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert _price(r, K1) == pytest.approx(3.0)
    assert _price(r, K2) == pytest.approx(9.0)
    assert r.procurement_cost == pytest.approx(210.0)
    assert r.paradoxically_accepted_buy_order_ids == ["w1_y"]
    assert verify_clearing(sells, buys, r) == []


# ===========================================================================
# Robustness: on general random markets (baskets with children, loops across
# windows, several products, buy families, price-capping buyers) the result
# must always pass the independent rule checker, in both modes.
# ===========================================================================

_GENERAL_WINDOWS = [(datetime(2026, 6, 15, h, 0), datetime(2026, 6, 15, h + 4, 0)) for h in (0, 4, 8, 12)]


def _general_random_market(seed):
    rng = random.Random(seed)
    sells, buys = [], []
    for b in range(rng.randint(2, 6)):
        window, product, unit = rng.choice(_GENERAL_WINDOWS), rng.choice(["DCL", "DCH"]), f"U{rng.randint(0, 3)}"
        sells.append(_sell(
            f"P{b}", unit, f"B{b}", "PARENT", rng.choice([5, 10, 20]), float(rng.randint(-3, 9)),
            product=product, window=window,
        ))
        for c in range(rng.randint(0, 2)):
            sells.append(_sell(
                f"C{b}_{c}", unit, f"B{b}", rng.choice(["CHILD", "SUBSTITUTABLECHILD"]), rng.choice([5, 10]),
                float(rng.randint(0, 9)), product=product, window=window,
            ))
    parents = [s for s in sells if s.order_type == "PARENT"]
    for i in range(0, len(parents) - 1, 3):  # loop some baskets together
        parents[i].looped_basket_id = parents[i + 1].basket_id
        parents[i + 1].looped_basket_id = parents[i].basket_id
    for j in range(rng.randint(3, 9)):
        buys.append(_buy(
            f"D{j}", rng.choice([5, 10, 15]), float(rng.randint(0, 12)), product=rng.choice(["DCL", "DCH"]),
            window=rng.choice(_GENERAL_WINDOWS), family=(f"F{rng.randint(0, 1)}" if rng.random() < 0.2 else None),
            paradox_ok=rng.random() > 0.4,
        ))
    return sells, buys


@pytest.mark.parametrize("seed", range(150))
def test_every_result_on_general_random_markets_passes_the_independent_checker(seed):
    sells, buys = _general_random_market(seed)
    welfare = {}
    for mode in (RESTRICTED, PERMISSIVE):
        result = clear_auction(sells, buys, overholding_mode=mode)  # raises if its own check fails
        assert verify_clearing(sells, buys, result, overholding_mode=mode) == [], f"seed {seed} {mode.value}"
        welfare[mode] = result.welfare
    assert welfare[PERMISSIVE] >= welfare[RESTRICTED] - 1e-6


# ===========================================================================
# Buy-side substitutability x pricing (Section 5.2, Appendix B)
#
# Run in isolation (from ingestion/):
#     pytest tests/test_eac_pricing.py -k buyfam -v   # these only
#     pytest tests -k buyfam -v                       # plus the acceptance-rule ones
#
# The family constraint (sum of acceptance ratios <= 1) decides WHICH buy
# orders clear; these tests check that the choice is consistent with Cost
# Minimisation Pricing, the paradoxical-acceptance flag and the overholding
# cap. Acceptance-rule tests (report Appendix B outcomes) live in
# test_eac_auction.py under the same -k buyfam selector.
# ===========================================================================

K_DCH = ("DCH", W1[0], W1[1])
W_B = (datetime(2026, 6, 15, 10, 0), datetime(2026, 6, 15, 14, 0))  # shares 10:00-12:00 with W1 (report B.3)
K_DCH_WB = ("DCH", W_B[0], W_B[1])


@pytest.mark.parametrize(
    "dcl_limit, winner, loser, price_key, idle_key, price",
    [
        (5.0, "B", "A", K1, K_DCH, 5.0),    # DCL: 200 - 50 = 150 beats DCH: 200 - 80 = 120
        (15.0, "A", "B", K_DCH, K1, 8.0),   # DCL: 200 - 150 = 50 loses to DCH: 120
    ],
    ids=["dcl-cheaper-wins", "dch-wins-when-dcl-seller-is-dear"],
)
def test_buyfam_family_picks_the_product_with_higher_welfare_and_prices_only_that_window(
    dcl_limit, winner, loser, price_key, idle_key, price
):
    """Family F = {A: 10MW DCH at 20, B: 10MW DCL at 20}; sellers: DCH 10MW
    at 8, DCL 10MW at `dcl_limit`. Exactly one member clears -- the product
    whose seller leaves more welfare. The winning window clears at its
    seller's limit (lowest feasible price); the idle window has no accepted
    volume and reports 0.0."""
    sells = [_parent("SH", "U1", 10, 8.0, product="DCH"), _parent("SL", "U2", 10, dcl_limit, product="DCL")]
    buys = [_buy("A", 10, 20.0, product="DCH", family="F"), _buy("B", 10, 20.0, product="DCL", family="F")]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id[winner] == 1.0
    assert r.buy_acceptance_ratio_by_order_id[loser] == 0.0
    assert r.clearing_price_by_product_window[price_key] == pytest.approx(price)
    assert r.clearing_price_by_product_window[idle_key] == 0.0
    assert verify_clearing(sells, buys, r) == []


@pytest.mark.parametrize("member_price", [20.0, 100.0])
def test_buyfam_family_member_limit_price_does_not_move_the_clearing_price(member_price):
    """Section 7.1.2: buy limit prices never set the price -- including
    those of family members. The winning DCL member bids 20 or 100; the
    price is the seller's limit, 5, either way."""
    sells = [_parent("SH", "U1", 10, 8.0, product="DCH"), _parent("SL", "U2", 10, 5.0, product="DCL")]
    buys = [_buy("A", 10, 20.0, product="DCH", family="F"), _buy("B", 10, member_price, product="DCL", family="F")]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id["B"] == 1.0
    assert r.clearing_price_by_product_window[K1] == pytest.approx(5.0)


@pytest.mark.parametrize("mode", [RESTRICTED, PERMISSIVE])
def test_buyfam_family_blocks_the_two_paradoxical_buyer_trade_even_in_permissive_mode(mode):
    """30MW non-curtailable at limit 3; buyers B1 10 at 10, and B2 (10 at
    2) and B3 (10 at 1) sharing family F. Without the family the permissive
    mode accepts this trade (welfare +40, see the overholding tests). With
    the family, B2 + B3 can supply at most ONE order's worth (10MW): 10 + 10
    = 20 < 30MW, so no mode can take it."""
    sells = [_parent("S", "U", 30, 3.0)]
    buys = [_buy("B1", 10, 10.0), _buy("B2", 10, 2.0, family="F"), _buy("B3", 10, 1.0, family="F")]
    r = clear_auction(sells, buys, overholding_mode=mode)
    assert r.acceptance_ratio_by_order_id["S"] == 0.0
    assert r.welfare == 0.0


def test_buyfam_control_the_same_trade_without_a_family_is_accepted_in_permissive_mode():
    """Pins that the family -- not something else -- blocked the trade above."""
    sells = [_parent("S", "U", 30, 3.0)]
    buys = [_buy("B1", 10, 10.0), _buy("B2", 10, 2.0), _buy("B3", 10, 1.0)]
    r = clear_auction(sells, buys, overholding_mode=PERMISSIVE)
    assert r.acceptance_ratio_by_order_id["S"] == 1.0
    assert r.welfare == pytest.approx(40.0)


def test_buyfam_restricted_mode_uses_the_higher_priced_family_member_as_its_one_paradoxical_buyer():
    """20MW at limit 3; buyer A 10 at 10; family F = {B: 10 at 2, C: 10 at
    1}. The 10MW shortfall must come from one family member, paradoxically
    (price 3 exceeds both limits) -- the single paradoxical slot the
    RESTRICTED cap allows. B pays more, so B is used: welfare 100 + 20 - 60
    = 60."""
    sells = [_parent("S", "U", 20, 3.0)]
    buys = [_buy("A", 10, 10.0), _buy("B", 10, 2.0, family="F"), _buy("C", 10, 1.0, family="F")]
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.buy_acceptance_ratio_by_order_id == {"A": 1.0, "B": 1.0, "C": 0.0}
    assert r.paradoxically_accepted_buy_order_ids == ["B"]
    assert _price(r) == pytest.approx(3.0)
    assert r.welfare == pytest.approx(60.0)


@pytest.mark.parametrize(
    "b_allows_paradox, expected_member, expected_welfare",
    [(True, "B", 940.0), (False, "C", 930.0)],
    ids=["both-allowed-B-wins", "B-forbids-paradox-C-used"],
)
def test_buyfam_paradox_flag_on_a_member_changes_which_member_the_family_uses(
    b_allows_paradox, expected_member, expected_welfare
):
    """20MW non-curtailable at limit 5; buyer D 10 at 100 (no family);
    family F = {B: 10 at 4, C: 10 at 3}. The price must be >= 5, above both
    members' limits, so whichever member is used is paradoxically accepted.
    If B allows that it wins on price (welfare 1000 + 40 - 100 = 940). If B
    forbids it, B is unusable and C -- which allows it -- is used even
    though it pays less (1000 + 30 - 100 = 930)."""
    sells = [_parent("S", "U", 20, 5.0)]
    buys = [
        _buy("D", 10, 100.0),
        _buy("B", 10, 4.0, family="F", paradox_ok=b_allows_paradox),
        _buy("C", 10, 3.0, family="F"),
    ]
    r = clear_auction(sells, buys, overholding_mode=RESTRICTED)
    assert r.buy_acceptance_ratio_by_order_id[expected_member] == 1.0
    other = "C" if expected_member == "B" else "B"
    assert r.buy_acceptance_ratio_by_order_id[other] == 0.0
    assert r.paradoxically_accepted_buy_order_ids == [expected_member]
    assert _price(r) == pytest.approx(5.0)
    assert r.welfare == pytest.approx(expected_welfare)
    assert verify_clearing(sells, buys, r) == []


def test_buyfam_family_across_overlapping_windows_prices_only_the_winning_window():
    """Report B.3: family F = {A: DCL in W1 at 100, B: DCH in a window that
    overlaps W1 at 60}; sellers 10MW each, limits 5 (W1) and 7 (other),
    different units. A wins (1000 - 50 = 950 vs 600 - 70 = 530). W1 clears at
    5; the other window has no accepted volume and reports 0.0."""
    sells = [
        _parent("S1", "U1", 10, 5.0, product="DCL", window=W1),
        _parent("S2", "U2", 10, 7.0, product="DCH", window=W_B),
    ]
    buys = [
        _buy("A", 10, 100.0, product="DCL", window=W1, family="F"),
        _buy("B", 10, 60.0, product="DCH", window=W_B, family="F"),
    ]
    r = clear_auction(sells, buys)
    assert r.buy_acceptance_ratio_by_order_id == {"A": 1.0, "B": 0.0}
    assert r.clearing_price_by_product_window[K1] == pytest.approx(5.0)
    assert r.clearing_price_by_product_window[K_DCH_WB] == 0.0
    assert r.welfare == pytest.approx(950.0)
