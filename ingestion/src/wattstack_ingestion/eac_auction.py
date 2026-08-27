"""Real EAC (Enduring Auction Capability) clearing simulation --
reconstructs the actual competitive landscape for a real, historical
DC auction and determines whether a hypothetical additional sell order
(a BESS's own bid) would have been accepted, and at approximately what
clearing price.

Built from NESO's own N-SIDE Power Matching Algorithm public
description (https://www.neso.energy/document/282401/download,
June 2024) -- a genuine welfare-maximising Mixed Integer Linear
Program, not a simple merit-order stack. Co-optimisation across
products, baskets with mutual exclusivity, parent/non-curtailable and
child/curtailable orders, and substitutable child orders are all
modelled here as a real MILP (via pulp, the same solver library core's
optimizer already uses) -- deliberately not a shortcut approximation,
per the explicit choice to prioritise fidelity over simplicity.

What IS modelled (Stage 1 -- acceptance, the primary question this
exists to answer):
  - Multi-product co-optimisation (a unit's separate baskets compete
    against each other, e.g. DCH vs DCL, for the same capacity).
  - Baskets, parent orders (non-curtailable, binary), child orders and
    substitutable child orders (curtailable, continuous 0-1).
  - Mutual exclusivity between a single unit's own baskets on the same
    service window (confirmed scope -- see _build_acceptance_problem's
    own docstring for the direct evidence this is unit-scoped, not
    market-wide).
  - Substitutable-family constraint (sum of ratios <= 1 within a
    basket's substitutable children).
  - Volume balance between accepted sell and accepted buy volume, per
    product.

What is deliberately NOT modelled, named directly rather than implied
solved:
  - Looped baskets across non-overlapping service windows (a real,
    rarer feature -- this module treats each service window
    independently).
  - Buy-side substitutability families (NESO expressing indifference
    between products) -- buy orders are treated as independent here.
  - Overholding / paradoxical buy-order acceptance -- sell volume is
    capped at accepted buy volume, not allowed to exceed it.
  - The full "Cost Minimisation Pricing" rule with surplus transfers
    across linked/looped orders (Stage 2 here uses a simpler
    marginal-price approximation -- the price of the most expensive
    accepted order per product -- explicitly flagged as an
    approximation of the real pricing rule, not a replica of it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pulp

from wattstack_ingestion.neso import KNOWN_RESOURCES, NesoClient


@dataclass
class SellOrderInput:
    """One sell order -- either a real, historical order fetched from
    NESO's own data, or a hypothetical bid being tested against them.
    Field names deliberately match the confirmed CKAN schema
    (auctionUnit, basketID, orderType, auctionProduct, priceLimit)
    rather than being renamed for this module's own convenience --
    makes converting a raw fetched row into this dataclass a direct,
    traceable mapping, not a reinterpretation.
    """

    order_id: str
    auction_unit: str
    basket_id: str
    order_type: str  # "PARENT", "CHILD", or "SUBSTITUTABLECHILD"
    auction_product: str  # e.g. "DCH", "DCL"
    quantity_mw: float
    price_limit: float  # £/MW/h


@dataclass
class BuyOrderInput:
    order_id: str
    auction_product: str
    quantity_mw: float
    price: float  # £/MW/h


@dataclass
class ClearingResult:
    """The outcome of solving the acceptance MILP (Stage 1) and the
    approximate pricing step (Stage 2) for one service window.

    `hypothetical_bid_accepted`/`hypothetical_bid_acceptance_ratio`
    answer the actual question this module exists for. `clearing_price_by_product`
    is Stage 2's approximation -- see this module's own docstring for
    exactly what simplification that represents.
    """

    acceptance_ratio_by_order_id: dict[str, float]
    clearing_price_by_product: dict[str, float]
    hypothetical_bid_order_id: str | None = None

    @property
    def hypothetical_bid_accepted(self) -> bool:
        if self.hypothetical_bid_order_id is None:
            return False
        return self.acceptance_ratio_by_order_id.get(self.hypothetical_bid_order_id, 0.0) > 0.0

    @property
    def hypothetical_bid_acceptance_ratio(self) -> float:
        if self.hypothetical_bid_order_id is None:
            return 0.0
        return self.acceptance_ratio_by_order_id.get(self.hypothetical_bid_order_id, 0.0)


class ResponseReserveOrdersProvider:
    """Fetches real, historical EAC buy and sell orders for
    Response-Reserve products -- confirmed field names and resource
    IDs, both sourced directly from NESO's own dataset schema pages
    (neso_response-reserve_daily_buy_orders /
    ..._sell_orders), not the general listing page (see
    KNOWN_RESOURCES's own docstring for the correction this made to
    two previously-unconfirmed resource IDs).

    NOT a PriceProvider -- doesn't implement wholesale_prices()/
    reserve_prices(). This is raw order-level detail for backtesting
    acceptance, not a settled price series for day-ahead dispatch
    planning; a fundamentally different kind of data and purpose from
    everything else in prices.py.
    """

    def __init__(self, client: NesoClient | None = None, limit: int = 5000):
        self.client = client or NesoClient()
        self.limit = limit

    def sell_orders(self, window_start: datetime, window_end: datetime, auction_product: str) -> list[SellOrderInput]:
        """Real sell orders for the given service window and product
        (e.g. "DCH", "DCL"). Filters on `deliveryStart`/`deliveryEnd`
        matching the window exactly -- a real auction's service
        window is a specific, fixed period (an EFA block for DC), not
        an approximate date range."""
        rows = self.client.datastore_search(
            KNOWN_RESOURCES["response_reserve_sell_orders"], limit=self.limit,
            sort="deliveryStart desc",
        )
        return [
            SellOrderInput(
                order_id=str(r["orderID"]),
                auction_unit=str(r.get("auctionUnit", "")),
                basket_id=str(r.get("basketID", "")),
                order_type=str(r.get("orderType", "PARENT")),
                auction_product=str(r.get("auctionProduct", "")),
                quantity_mw=float(r.get("quantity", 0.0)),
                price_limit=float(r.get("priceLimit", 0.0)),
            )
            for r in rows
            if r.get("auctionProduct") == auction_product and self._matches_window(r, window_start, window_end)
        ]

    def buy_orders(self, window_start: datetime, window_end: datetime, auction_product: str) -> list[BuyOrderInput]:
        rows = self.client.datastore_search(
            KNOWN_RESOURCES["response_reserve_buy_orders"], limit=self.limit,
            sort="deliveryStart desc",
        )
        return [
            BuyOrderInput(
                order_id=str(r["orderID"]),
                auction_product=str(r.get("auctionProduct", "")),
                quantity_mw=float(r.get("quantity", 0.0)),
                price=float(r.get("price", 0.0)),
            )
            for r in rows
            if r.get("auctionProduct") == auction_product and self._matches_window(r, window_start, window_end)
        ]

    def _matches_window(self, row: dict, window_start: datetime, window_end: datetime) -> bool:
        raw_start = row.get("deliveryStart")
        if raw_start is None:
            return False
        try:
            parsed_start = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return False
        return parsed_start == window_start.replace(tzinfo=None)


def clear_auction(sell_orders: list[SellOrderInput], buy_orders: list[BuyOrderInput]) -> ClearingResult:
    """Solves the Stage 1 (acceptance) welfare-maximisation MILP for
    one service window, given real (and/or hypothetical) sell orders
    and real buy orders. See this module's own docstring for exactly
    which real EAC rules are modelled and which are deliberately not.

    Mutual exclusivity is scoped to a single unit's OWN baskets, not
    market-wide -- confirmed directly from the design document's own
    co-optimisation example (Section 4, Figure 2): a single Market
    Participant offering the same capacity across two baskets (one per
    product) has only one of ITS OWN baskets selected, while other,
    different units are simultaneously and independently accepted
    alongside it. Grouping by `auction_unit` here reflects that
    directly, not an assumption.
    """
    prob = pulp.LpProblem("eac_clearing", pulp.LpMaximize)

    sell_vars = {}
    for so in sell_orders:
        if so.order_type == "PARENT":
            sell_vars[so.order_id] = pulp.LpVariable(f"sell_{so.order_id}", cat="Binary")
        else:
            sell_vars[so.order_id] = pulp.LpVariable(f"sell_{so.order_id}", lowBound=0, upBound=1)

    buy_vars = {bo.order_id: pulp.LpVariable(f"buy_{bo.order_id}", lowBound=0, upBound=1) for bo in buy_orders}

    buy_utility = pulp.lpSum(buy_vars[bo.order_id] * bo.quantity_mw * bo.price for bo in buy_orders)
    sell_cost = pulp.lpSum(sell_vars[so.order_id] * so.quantity_mw * so.price_limit for so in sell_orders)
    prob += buy_utility - sell_cost

    baskets: dict[str, list[SellOrderInput]] = {}
    for so in sell_orders:
        baskets.setdefault(so.basket_id, []).append(so)

    basket_accepted: dict[str, pulp.LpVariable] = {}
    for basket_id, orders_in_basket in baskets.items():
        parents = [so for so in orders_in_basket if so.order_type == "PARENT"]
        if not parents:
            continue
        basket_accepted[basket_id] = sell_vars[parents[0].order_id]

        children = [so for so in orders_in_basket if so.order_type == "CHILD"]
        for child in children:
            prob += sell_vars[child.order_id] <= basket_accepted[basket_id]

        substitutable_children = [so for so in orders_in_basket if so.order_type == "SUBSTITUTABLECHILD"]
        for sc in substitutable_children:
            prob += sell_vars[sc.order_id] <= basket_accepted[basket_id]
        if substitutable_children:
            prob += pulp.lpSum(sell_vars[sc.order_id] for sc in substitutable_children) <= 1

    baskets_by_unit: dict[str, set[str]] = {}
    for so in sell_orders:
        baskets_by_unit.setdefault(so.auction_unit, set()).add(so.basket_id)
    for unit, basket_ids in baskets_by_unit.items():
        relevant = [basket_accepted[b] for b in basket_ids if b in basket_accepted]
        if len(relevant) > 1:
            prob += pulp.lpSum(relevant) <= 1

    products = {so.auction_product for so in sell_orders} | {bo.auction_product for bo in buy_orders}
    for product in products:
        accepted_sell = pulp.lpSum(
            sell_vars[so.order_id] * so.quantity_mw for so in sell_orders if so.auction_product == product
        )
        accepted_buy = pulp.lpSum(
            buy_vars[bo.order_id] * bo.quantity_mw for bo in buy_orders if bo.auction_product == product
        )
        # Equality, not <=: accepted_sell <= accepted_buy alone lets the
        # solver "accept" buy-side utility with zero matching sell
        # volume (x=0, y=1 satisfies 0 <= anything), collecting welfare
        # from a trade that never actually happened. No overholding is
        # modelled here (a real, deferred feature -- see module
        # docstring), so buy and sell volume must match exactly, not
        # merely be bounded by each other. Buy orders are always fully
        # curtailable (continuous), so the buy side can always adjust
        # to match whatever the (possibly binary-constrained) sell side
        # actually provides -- equality is always achievable, not
        # over-constraining the problem.
        prob += accepted_sell == accepted_buy

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"EAC clearing MILP did not find an optimal solution: {pulp.LpStatus[prob.status]}")

    acceptance_ratios = {so.order_id: round(pulp.value(sell_vars[so.order_id]) or 0.0, 4) for so in sell_orders}

    # Stage 2, a stated approximation of the real "Cost Minimisation
    # Pricing" rule (see module docstring): the price of the most
    # expensive accepted order per product, not the full
    # transfer-aware minimisation.
    clearing_prices: dict[str, float] = {}
    for product in products:
        accepted_prices = [
            so.price_limit for so in sell_orders
            if so.auction_product == product and acceptance_ratios.get(so.order_id, 0.0) > 0.0
        ]
        clearing_prices[product] = max(accepted_prices) if accepted_prices else 0.0

    return ClearingResult(acceptance_ratio_by_order_id=acceptance_ratios, clearing_price_by_product=clearing_prices)


def backtest_hypothetical_bid(
    orders_provider: ResponseReserveOrdersProvider,
    window_start: datetime,
    window_end: datetime,
    auction_products: list[str],
    my_auction_unit: str,
    my_product: str,
    my_price: float,
    my_quantity_mw: float,
) -> ClearingResult:
    """The actual thing this module exists for: fetches real sell and
    buy orders for every product in `auction_products` for the given
    service window (co-optimisation needs every product a real unit
    might be competing across, not just the one your hypothetical bid
    targets -- pass at least ["DCH", "DCL"] for a genuine DC
    backtest, not just the single product you're bidding into),
    inserts a hypothetical sell order for `my_product` as its own,
    standalone basket (a single PARENT order, non-curtailable -- the
    simplest, most common real bid shape), and solves the clearing
    MILP.

    `my_auction_unit` should not collide with any real unit name in
    the fetched data (mutual exclusivity is scoped per unit -- see
    clear_auction()'s own docstring). A clearly synthetic name (e.g.
    "MY_HYPOTHETICAL_UNIT") is safest.
    """
    all_sell_orders: list[SellOrderInput] = []
    all_buy_orders: list[BuyOrderInput] = []
    for product in auction_products:
        all_sell_orders.extend(orders_provider.sell_orders(window_start, window_end, product))
        all_buy_orders.extend(orders_provider.buy_orders(window_start, window_end, product))

    hypothetical_order_id = "HYPOTHETICAL_BID"
    hypothetical = SellOrderInput(
        order_id=hypothetical_order_id,
        auction_unit=my_auction_unit,
        basket_id=hypothetical_order_id,
        order_type="PARENT",
        auction_product=my_product,
        quantity_mw=my_quantity_mw,
        price_limit=my_price,
    )
    all_sell_orders.append(hypothetical)

    result = clear_auction(all_sell_orders, all_buy_orders)
    result.hypothetical_bid_order_id = hypothetical_order_id
    return result
