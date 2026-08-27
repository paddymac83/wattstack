"""No real network calls -- requests.get is mocked throughout."""
from datetime import datetime
from unittest.mock import MagicMock, patch

from wattstack_ingestion.eac_auction import (
    BuyOrderInput,
    ClearingResult,
    ResponseReserveOrdersProvider,
    SellOrderInput,
    backtest_hypothetical_bid,
    clear_auction,
)
from wattstack_ingestion.neso import NesoClient


def _sell(order_id, unit, basket, order_type, product, quantity, price):
    return SellOrderInput(
        order_id=order_id, auction_unit=unit, basket_id=basket, order_type=order_type,
        auction_product=product, quantity_mw=quantity, price_limit=price,
    )


def _buy(order_id, product, quantity, price):
    return BuyOrderInput(order_id=order_id, auction_product=product, quantity_mw=quantity, price=price)


# --- clear_auction: basic welfare maximisation ---


def test_cheaper_sell_order_accepted_over_more_expensive_one_when_volume_limited():
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0),   # cheap
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 50.0),  # expensive
    ]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0)]  # only enough volume for one 10MW order
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 0.0


def test_accepted_sell_volume_never_exceeds_accepted_buy_volume():
    sell_orders = [_sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 100, 1.0)]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]  # far less volume wanted than offered
    result = clear_auction(sell_orders, buy_orders)
    accepted_mw = result.acceptance_ratio_by_order_id["S1"] * 100
    assert accepted_mw <= 20 + 1e-6


def test_sell_order_priced_above_all_buy_willingness_is_rejected():
    sell_orders = [_sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 500.0)]
    buy_orders = [_buy("BUY1", "DCL", 10, 100.0)]  # buyer won't pay 500 -- would only lose welfare
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 0.0


# --- mutual exclusivity: within a unit's own baskets, not market-wide ---


def test_single_unit_two_baskets_only_one_accepted():
    """Confirms the design document's own Figure 2 example directly:
    a single unit offering the same capacity across two baskets (one
    per product) gets only one selected, never both."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCH", 10, 5.0),
        _sell("S2", "UNIT_A", "B2", "PARENT", "DCL", 10, 5.0),  # same unit, different basket, both cheap
    ]
    buy_orders = [_buy("BUY_H", "DCH", 10, 100.0), _buy("BUY_L", "DCL", 10, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    total_accepted = result.acceptance_ratio_by_order_id["S1"] + result.acceptance_ratio_by_order_id["S2"]
    assert total_accepted <= 1.0 + 1e-6


def test_co_optimisation_picks_the_more_valuable_product_for_a_single_unit():
    """When a unit's two baskets are both cheap enough to be accepted
    individually, welfare maximisation should pick whichever product
    the buy side values more -- not an arbitrary one."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCH", 10, 5.0),
        _sell("S2", "UNIT_A", "B2", "PARENT", "DCL", 10, 5.0),
    ]
    buy_orders = [_buy("BUY_H", "DCH", 10, 10.0), _buy("BUY_L", "DCL", 10, 1000.0)]  # DCL far more valuable
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S2"] == 1.0  # DCL (S2) selected
    assert result.acceptance_ratio_by_order_id["S1"] == 0.0  # DCH (S1) rejected


def test_different_units_are_not_mutually_exclusive():
    """The mirror image of the single-unit test: two DIFFERENT units,
    both cheap, should both be accepted simultaneously -- proving
    mutual exclusivity is genuinely scoped per-unit, not accidentally
    applied market-wide."""
    sell_orders = [
        _sell("S1", "UNIT_A", "B1", "PARENT", "DCL", 10, 5.0),
        _sell("S2", "UNIT_B", "B2", "PARENT", "DCL", 10, 5.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]  # enough volume for both
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["S1"] == 1.0
    assert result.acceptance_ratio_by_order_id["S2"] == 1.0


# --- parent/child linkage and substitutable families ---


def test_child_order_cannot_be_accepted_if_parent_rejected():
    sell_orders = [
        _sell("PARENT1", "UNIT_A", "B1", "PARENT", "DCL", 10, 500.0),  # too expensive, will be rejected
        _sell("CHILD1", "UNIT_A", "B1", "CHILD", "DCL", 5, 1.0),        # cheap, but linked to the parent
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["PARENT1"] == 0.0
    assert result.acceptance_ratio_by_order_id["CHILD1"] == 0.0  # cannot be accepted without its parent


def test_child_order_accepted_when_parent_is_accepted():
    sell_orders = [
        _sell("PARENT1", "UNIT_A", "B1", "PARENT", "DCL", 10, 1.0),
        _sell("CHILD1", "UNIT_A", "B1", "CHILD", "DCL", 5, 1.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]
    result = clear_auction(sell_orders, buy_orders)
    assert result.acceptance_ratio_by_order_id["PARENT1"] == 1.0
    assert result.acceptance_ratio_by_order_id["CHILD1"] == 1.0


def test_substitutable_children_sum_of_acceptance_ratios_never_exceeds_one():
    sell_orders = [
        _sell("PARENT1", "UNIT_A", "B1", "PARENT", "DCL", 0, 1.0),  # zero-MW parent, fully curtailable basket
        _sell("SC1", "UNIT_A", "B1", "SUBSTITUTABLECHILD", "DCL", 10, 1.0),
        _sell("SC2", "UNIT_A", "B1", "SUBSTITUTABLECHILD", "DCL", 10, 1.0),
    ]
    buy_orders = [_buy("BUY1", "DCL", 20, 100.0)]  # enough for BOTH if the constraint didn't apply
    result = clear_auction(sell_orders, buy_orders)
    total = result.acceptance_ratio_by_order_id["SC1"] + result.acceptance_ratio_by_order_id["SC2"]
    assert total <= 1.0 + 1e-6


# --- ClearingResult / hypothetical bid properties ---


def test_clearing_result_hypothetical_bid_accepted_true_when_ratio_positive():
    result = ClearingResult(
        acceptance_ratio_by_order_id={"HYPOTHETICAL_BID": 1.0}, clearing_price_by_product={},
        hypothetical_bid_order_id="HYPOTHETICAL_BID",
    )
    assert result.hypothetical_bid_accepted is True
    assert result.hypothetical_bid_acceptance_ratio == 1.0


def test_clearing_result_hypothetical_bid_accepted_false_when_ratio_zero():
    result = ClearingResult(
        acceptance_ratio_by_order_id={"HYPOTHETICAL_BID": 0.0}, clearing_price_by_product={},
        hypothetical_bid_order_id="HYPOTHETICAL_BID",
    )
    assert result.hypothetical_bid_accepted is False


def test_clearing_result_no_hypothetical_bid_id_gives_false_not_a_crash():
    result = ClearingResult(acceptance_ratio_by_order_id={}, clearing_price_by_product={})
    assert result.hypothetical_bid_accepted is False
    assert result.hypothetical_bid_acceptance_ratio == 0.0


def test_backtest_hypothetical_bid_cheap_bid_gets_accepted():
    orders_provider = MagicMock()
    orders_provider.sell_orders.return_value = [_sell("REAL1", "UNIT_A", "B1", "PARENT", "DCL", 10, 50.0)]
    orders_provider.buy_orders.return_value = [_buy("BUY1", "DCL", 30, 100.0)]  # room for both real and hypothetical

    result = backtest_hypothetical_bid(
        orders_provider, datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0),
        auction_products=["DCL"], my_auction_unit="MY_UNIT", my_product="DCL",
        my_price=1.0, my_quantity_mw=5,
    )
    assert result.hypothetical_bid_accepted is True


def test_backtest_hypothetical_bid_expensive_bid_gets_rejected():
    orders_provider = MagicMock()
    orders_provider.sell_orders.return_value = [_sell("REAL1", "UNIT_A", "B1", "PARENT", "DCL", 30, 5.0)]
    orders_provider.buy_orders.return_value = [_buy("BUY1", "DCL", 30, 100.0)]  # exactly filled by the cheap real order

    result = backtest_hypothetical_bid(
        orders_provider, datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0),
        auction_products=["DCL"], my_auction_unit="MY_UNIT", my_product="DCL",
        my_price=90.0, my_quantity_mw=5,
    )
    assert result.hypothetical_bid_accepted is False


def test_backtest_hypothetical_bid_fetches_every_product_for_co_optimisation():
    orders_provider = MagicMock()
    orders_provider.sell_orders.return_value = []
    orders_provider.buy_orders.return_value = []

    backtest_hypothetical_bid(
        orders_provider, datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0),
        auction_products=["DCH", "DCL"], my_auction_unit="MY_UNIT", my_product="DCL",
        my_price=10.0, my_quantity_mw=5,
    )
    fetched_products = {call.args[2] for call in orders_provider.sell_orders.call_args_list}
    assert fetched_products == {"DCH", "DCL"}


# --- ResponseReserveOrdersProvider: field mapping and filtering ---


def _mock_response(json_body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@patch("wattstack_ingestion.neso.requests.get")
def test_sell_orders_maps_confirmed_fields_correctly(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {
            "orderID": 7, "auctionUnit": "AG-GEDF02", "basketID": 3, "orderType": "PARENT",
            "auctionProduct": "DCL", "quantity": 5, "priceLimit": 12.5,
            "deliveryStart": "2026-06-15T08:00:00Z", "deliveryEnd": "2026-06-15T12:00:00Z",
        },
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.sell_orders(datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0), "DCL")
    assert len(result) == 1
    assert result[0].order_id == "7"
    assert result[0].auction_unit == "AG-GEDF02"
    assert result[0].order_type == "PARENT"
    assert result[0].quantity_mw == 5.0
    assert result[0].price_limit == 12.5


@patch("wattstack_ingestion.neso.requests.get")
def test_sell_orders_filters_by_product(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"orderID": 1, "auctionUnit": "U1", "basketID": 1, "orderType": "PARENT",
         "auctionProduct": "DCH", "quantity": 5, "priceLimit": 1.0,
         "deliveryStart": "2026-06-15T08:00:00Z"},
        {"orderID": 2, "auctionUnit": "U2", "basketID": 2, "orderType": "PARENT",
         "auctionProduct": "DCL", "quantity": 5, "priceLimit": 1.0,
         "deliveryStart": "2026-06-15T08:00:00Z"},
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.sell_orders(datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0), "DCL")
    assert len(result) == 1
    assert result[0].order_id == "2"


@patch("wattstack_ingestion.neso.requests.get")
def test_sell_orders_filters_by_exact_window_start(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"orderID": 1, "auctionUnit": "U1", "basketID": 1, "orderType": "PARENT",
         "auctionProduct": "DCL", "quantity": 5, "priceLimit": 1.0,
         "deliveryStart": "2026-06-15T12:00:00Z"},  # different window
        {"orderID": 2, "auctionUnit": "U2", "basketID": 2, "orderType": "PARENT",
         "auctionProduct": "DCL", "quantity": 5, "priceLimit": 1.0,
         "deliveryStart": "2026-06-15T08:00:00Z"},  # matching window
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.sell_orders(datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0), "DCL")
    assert len(result) == 1
    assert result[0].order_id == "2"


@patch("wattstack_ingestion.neso.requests.get")
def test_buy_orders_maps_confirmed_fields_correctly(mock_get):
    mock_get.return_value = _mock_response({"success": True, "result": {"records": [
        {"orderID": 1, "auctionProduct": "DCH", "quantity": 10, "price": 100.0,
         "deliveryStart": "2026-06-15T08:00:00Z"},
    ]}})
    provider = ResponseReserveOrdersProvider(client=NesoClient())
    result = provider.buy_orders(datetime(2026, 6, 15, 8, 0), datetime(2026, 6, 15, 12, 0), "DCH")
    assert len(result) == 1
    assert result[0].order_id == "1"
    assert result[0].quantity_mw == 10.0
    assert result[0].price == 100.0
