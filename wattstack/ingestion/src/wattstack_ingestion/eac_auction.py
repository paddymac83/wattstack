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
child/curtailable orders, substitutable child orders, looped baskets,
buy-side substitutability, and overholding are all modelled here as a
real MILP (via pulp, the same solver library core's optimizer already
uses) -- deliberately not a shortcut approximation, per the explicit
choice to prioritise fidelity over simplicity.

Orders now carry their own explicit service window
(`window_start`/`window_end`), not an implicit single window assumed
by the caller -- required for looping (which links baskets across
non-overlapping windows) and buy-side substitutability (which can span
concomitant, not necessarily identical, windows) to mean anything at
all. `clear_auction()` therefore clears every window present in its
input JOINTLY, as one combined MILP, not one window at a time.

What IS modelled (Stage 1 -- acceptance, the primary question this
exists to answer):
  - Multi-product co-optimisation (a unit's separate baskets compete
    against each other, e.g. DCH vs DCL, for the same capacity).
  - Baskets, parent orders (non-curtailable, binary), child orders and
    substitutable child orders (curtailable, continuous 0-1).
  - Mutual exclusivity between a single unit's own baskets on
    OVERLAPPING service windows -- now based on genuine window overlap
    (start1 < end2 and start2 < end1), not an assumption that every
    basket in one call shares the same window.
  - Substitutable-family constraint on the sell side (sum of ratios
    <= 1 within a basket's substitutable children).
  - Looped baskets: baskets linked via `looped_basket_id` form a
    family (computed as connected components of the links, so a chain
    A-B-C or a star all-point-to-A representation both work correctly)
    whose acceptance ratios must all be equal -- either the whole loop
    clears or none of it does.
  - Buy-side substitutability: buy orders sharing a
    `substitutability_family` have their acceptance ratios summing to
    at most 1, the direct mirror of sell-side substitutable children.
  - Overholding: no special-cased mechanism needed beyond correct
    handling of a zero-priced buy order (see `overholding_buy_order()`
    below) -- a price of 0 contributes nothing to the objective, so
    welfare maximisation only uses it when a negatively-priced sell
    order makes doing so genuinely beneficial, matching the real
    design's own "at most one paradoxically accepted buy order"
    approach (Section 5.3.1).
  - Volume balance between accepted sell and accepted buy volume, now
    per (product, window) rather than per product alone, since a
    single call can legitimately span multiple windows.

What is deliberately NOT modelled, named directly rather than implied
solved:
  - The full "Cost Minimisation Pricing" rule with surplus transfers
    across linked/looped/substitutable orders (Stage 2 here uses a
    simpler marginal-price approximation -- the price of the most
    expensive accepted order per product per window -- explicitly
    flagged as an approximation of the real pricing rule, not a
    replica of it).
  - Explicit non-negative-surplus enforcement for baskets and looped
    families (Section 7.1.1's rules 7-8) -- these are fundamentally
    pricing-stage (Stage 2) concerns given they depend on the clearing
    price, and Stage 2 here is already a stated approximation rather
    than the real rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pulp

from wattstack_ingestion.neso import KNOWN_RESOURCES, NesoClient


@dataclass
class SellOrderInput:
    """One sell order -- either a real, historical order fetched from
    NESO's own data, or a hypothetical bid being tested against them.
    Field names deliberately match the confirmed CKAN schema
    (auctionUnit, basketID, orderType, auctionProduct, priceLimit,
    loopedBasketID) rather than being renamed for this module's own
    convenience -- makes converting a raw fetched row into this
    dataclass a direct, traceable mapping, not a reinterpretation.
    """

    order_id: str
    auction_unit: str
    basket_id: str
    order_type: str  # "PARENT", "CHILD", or "SUBSTITUTABLECHILD"
    auction_product: str  # e.g. "DCH", "DCL"
    quantity_mw: float
    price_limit: float  # £/MW/h
    window_start: datetime
    window_end: datetime
    looped_basket_id: str | None = None


@dataclass
class BuyOrderInput:
    order_id: str
    auction_product: str
    quantity_mw: float
    price: float  # £/MW/h
    window_start: datetime
    window_end: datetime
    substitutability_family: str | None = None


@dataclass
class ClearingResult:
    """The outcome of solving the acceptance MILP (Stage 1) and the
    approximate pricing step (Stage 2), potentially across multiple
    service windows cleared jointly.

    `hypothetical_bid_accepted`/`hypothetical_bid_acceptance_ratio`
    answer the actual question this module exists for.
    `clearing_price_by_product_window` is Stage 2's approximation --
    keyed by (product, window_start, window_end), not just product,
    since different windows of the same product clear at different
    prices. See this module's own docstring for exactly what
    simplification Stage 2 represents.
    """

    acceptance_ratio_by_order_id: dict[str, float]
    clearing_price_by_product_window: dict[tuple[str, datetime, datetime], float]
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


def overholding_buy_order(
    order_id: str, auction_product: str, max_overhold_mw: float, window_start: datetime, window_end: datetime,
) -> BuyOrderInput:
    """Constructs the synthetic buy order the design document itself
    describes for modelling overholding (Section 5.3.1, "allowing at
    most one paradoxically accepted buy order"): price 0 (so it never
    influences the objective directly) and volume equal to the
    maximum the buyer agrees to over-procure. Only ever gets used by
    welfare maximisation when a negatively-priced sell order exists
    for this product/window -- otherwise accepting more sell volume
    only adds cost with no offsetting utility, and the solver
    correctly leaves this order at 0.

    Not always necessary to call this directly: if NESO's own
    historical buy orders already include a zero-priced order for a
    given product/window (a real overholding order they actually
    submitted), fetching real data already captures it -- this helper
    is for constructing a hypothetical "what if more overholding were
    allowed" scenario, not a replacement for real data that already
    has it.
    """
    return BuyOrderInput(
        order_id=order_id, auction_product=auction_product, quantity_mw=max_overhold_mw, price=0.0,
        window_start=window_start, window_end=window_end,
    )


def _windows_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    return start1 < end2 and start2 < end1


def _looped_families(sell_orders: list[SellOrderInput]) -> list[set[str]]:
    """Groups basket IDs into looped families via `looped_basket_id`
    links, treated as an undirected graph with connected components --
    handles either a chain (A links to B, B links to C) or a star
    (B and C both link to A) representation of a multi-basket loop
    correctly, without assuming which one the real data actually
    uses. Only baskets genuinely linked to at least one other basket
    are returned (singletons -- unlooped baskets -- are omitted, since
    they need no equality constraint)."""
    parent_orders = [so for so in sell_orders if so.order_type == "PARENT"]
    adjacency: dict[str, set[str]] = {}
    for so in parent_orders:
        adjacency.setdefault(so.basket_id, set())
        if so.looped_basket_id:
            adjacency.setdefault(so.looped_basket_id, set())
            adjacency[so.basket_id].add(so.looped_basket_id)
            adjacency[so.looped_basket_id].add(so.basket_id)

    visited: set[str] = set()
    families: list[set[str]] = []
    for basket_id in adjacency:
        if basket_id in visited:
            continue
        component: set[str] = set()
        stack = [basket_id]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        visited |= component
        if len(component) > 1:
            families.append(component)
    return families


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

    def sell_orders(self, windows: list[tuple[datetime, datetime]], auction_product: str) -> list[SellOrderInput]:
        """Real sell orders across every window in `windows`, for the
        given product (e.g. "DCH", "DCL"). Pass multiple windows (e.g.
        a whole day's worth of EFA blocks) to correctly capture looped
        baskets or substitutable families that span more than the one
        window you're immediately interested in -- a single-window
        fetch cannot see a loop's other legs at all."""
        rows = self.client.datastore_search(
            KNOWN_RESOURCES["response_reserve_sell_orders"], limit=self.limit,
            sort="deliveryStart desc",
        )
        results = []
        for r in rows:
            if r.get("auctionProduct") != auction_product:
                continue
            window = self._matching_window(r, windows)
            if window is None:
                continue
            results.append(SellOrderInput(
                order_id=str(r["orderID"]),
                auction_unit=str(r.get("auctionUnit", "")),
                basket_id=str(r.get("basketID", "")),
                order_type=str(r.get("orderType", "PARENT")),
                auction_product=str(r.get("auctionProduct", "")),
                quantity_mw=float(r.get("quantity", 0.0)),
                price_limit=float(r.get("priceLimit", 0.0)),
                window_start=window[0],
                window_end=window[1],
                looped_basket_id=(str(r["loopedBasketID"]) if r.get("loopedBasketID") not in (None, "") else None),
            ))
        return results

    def buy_orders(self, windows: list[tuple[datetime, datetime]], auction_product: str) -> list[BuyOrderInput]:
        rows = self.client.datastore_search(
            KNOWN_RESOURCES["response_reserve_buy_orders"], limit=self.limit,
            sort="deliveryStart desc",
        )
        results = []
        for r in rows:
            if r.get("auctionProduct") != auction_product:
                continue
            window = self._matching_window(r, windows)
            if window is None:
                continue
            results.append(BuyOrderInput(
                order_id=str(r["orderID"]),
                auction_product=str(r.get("auctionProduct", "")),
                quantity_mw=float(r.get("quantity", 0.0)),
                price=float(r.get("price", 0.0)),
                window_start=window[0],
                window_end=window[1],
                substitutability_family=(
                    str(r["substitutabilityFamily"]) if r.get("substitutabilityFamily") not in (None, "") else None
                ),
            ))
        return results

    def _matching_window(
        self, row: dict, windows: list[tuple[datetime, datetime]]
    ) -> tuple[datetime, datetime] | None:
        raw_start = row.get("deliveryStart")
        if raw_start is None:
            return None
        try:
            parsed_start = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
        for window_start, window_end in windows:
            if parsed_start == window_start.replace(tzinfo=None):
                return (window_start, window_end)
        return None


def clear_auction(sell_orders: list[SellOrderInput], buy_orders: list[BuyOrderInput]) -> ClearingResult:
    """Solves the Stage 1 (acceptance) welfare-maximisation MILP,
    jointly across every service window present in the given orders.
    See this module's own docstring for exactly which real EAC rules
    are modelled and which are deliberately not.

    Mutual exclusivity is scoped to a single unit's own baskets on
    OVERLAPPING windows, not market-wide -- confirmed directly from
    the design document's own co-optimisation example (Section 4,
    Figure 2): a single Market Participant offering the same capacity
    across two baskets (one per product) has only one of ITS OWN
    baskets selected, while other, different units are simultaneously
    and independently accepted alongside it.
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
    basket_window: dict[str, tuple[datetime, datetime]] = {}
    basket_unit: dict[str, str] = {}
    for basket_id, orders_in_basket in baskets.items():
        parents = [so for so in orders_in_basket if so.order_type == "PARENT"]
        if not parents:
            continue
        parent = parents[0]
        basket_accepted[basket_id] = sell_vars[parent.order_id]
        basket_window[basket_id] = (parent.window_start, parent.window_end)
        basket_unit[basket_id] = parent.auction_unit

        children = [so for so in orders_in_basket if so.order_type == "CHILD"]
        for child in children:
            prob += sell_vars[child.order_id] <= basket_accepted[basket_id]

        substitutable_children = [so for so in orders_in_basket if so.order_type == "SUBSTITUTABLECHILD"]
        for sc in substitutable_children:
            prob += sell_vars[sc.order_id] <= basket_accepted[basket_id]
        if substitutable_children:
            prob += pulp.lpSum(sell_vars[sc.order_id] for sc in substitutable_children) <= 1

    unit_baskets: dict[str, list[str]] = {}
    for basket_id, unit in basket_unit.items():
        unit_baskets.setdefault(unit, []).append(basket_id)
    for unit, basket_ids in unit_baskets.items():
        for i in range(len(basket_ids)):
            for j in range(i + 1, len(basket_ids)):
                b1, b2 = basket_ids[i], basket_ids[j]
                s1, e1 = basket_window[b1]
                s2, e2 = basket_window[b2]
                if _windows_overlap(s1, e1, s2, e2):
                    prob += basket_accepted[b1] + basket_accepted[b2] <= 1

    for family in _looped_families(sell_orders):
        family_list = sorted(family)
        anchor = family_list[0]
        for other in family_list[1:]:
            if anchor in basket_accepted and other in basket_accepted:
                prob += basket_accepted[anchor] == basket_accepted[other]

    buy_families: dict[str, list[BuyOrderInput]] = {}
    for bo in buy_orders:
        if bo.substitutability_family:
            buy_families.setdefault(bo.substitutability_family, []).append(bo)
    for family_id, orders_in_family in buy_families.items():
        prob += pulp.lpSum(buy_vars[bo.order_id] for bo in orders_in_family) <= 1

    window_products: set[tuple[str, datetime, datetime]] = set()
    for so in sell_orders:
        window_products.add((so.auction_product, so.window_start, so.window_end))
    for bo in buy_orders:
        window_products.add((bo.auction_product, bo.window_start, bo.window_end))

    for product, w_start, w_end in window_products:
        accepted_sell = pulp.lpSum(
            sell_vars[so.order_id] * so.quantity_mw for so in sell_orders
            if so.auction_product == product and so.window_start == w_start and so.window_end == w_end
        )
        accepted_buy = pulp.lpSum(
            buy_vars[bo.order_id] * bo.quantity_mw for bo in buy_orders
            if bo.auction_product == product and bo.window_start == w_start and bo.window_end == w_end
        )
        # Equality, not <=: accepted_sell <= accepted_buy alone lets the
        # solver "accept" buy-side utility with zero matching sell
        # volume, collecting welfare from a trade that never actually
        # happened (see docs/adr/0030 for the real bug this caused
        # before it was caught and fixed). Overholding doesn't need a
        # different rule here -- it's modelled entirely by a
        # zero-priced buy order adding volume to `accepted_buy`, not
        # by relaxing this equality.
        prob += accepted_sell == accepted_buy

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"EAC clearing MILP did not find an optimal solution: {pulp.LpStatus[prob.status]}")

    acceptance_ratios = {so.order_id: round(pulp.value(sell_vars[so.order_id]) or 0.0, 4) for so in sell_orders}

    # Stage 2, a stated approximation of the real "Cost Minimisation
    # Pricing" rule (see module docstring): the price of the most
    # expensive accepted order per product per window, not the full
    # transfer-aware minimisation.
    clearing_prices: dict[tuple[str, datetime, datetime], float] = {}
    for product, w_start, w_end in window_products:
        accepted_prices = [
            so.price_limit for so in sell_orders
            if so.auction_product == product and so.window_start == w_start and so.window_end == w_end
            and acceptance_ratios.get(so.order_id, 0.0) > 0.0
        ]
        clearing_prices[(product, w_start, w_end)] = max(accepted_prices) if accepted_prices else 0.0

    return ClearingResult(
        acceptance_ratio_by_order_id=acceptance_ratios, clearing_price_by_product_window=clearing_prices
    )


def backtest_hypothetical_bid(
    orders_provider: ResponseReserveOrdersProvider,
    windows: list[tuple[datetime, datetime]],
    auction_products: list[str],
    my_auction_unit: str,
    my_product: str,
    my_price: float,
    my_quantity_mw: float,
    my_window: tuple[datetime, datetime] | None = None,
) -> ClearingResult:
    """The actual thing this module exists for: fetches real sell and
    buy orders for every product in `auction_products`, across every
    window in `windows` (pass more than just your own target window --
    e.g. a whole day's worth of EFA blocks -- so any real competitor's
    looped baskets or substitutable families that extend beyond your
    own window are correctly captured, not silently truncated),
    inserts a hypothetical sell order for `my_product` as its own,
    standalone basket (a single PARENT order, non-curtailable -- the
    simplest, most common real bid shape) in `my_window` (defaults to
    `windows[0]` if not given), and solves the joint clearing MILP.

    `my_auction_unit` should not collide with any real unit name in
    the fetched data (mutual exclusivity is scoped per unit -- see
    clear_auction()'s own docstring). A clearly synthetic name (e.g.
    "MY_HYPOTHETICAL_UNIT") is safest.
    """
    all_sell_orders: list[SellOrderInput] = []
    all_buy_orders: list[BuyOrderInput] = []
    for product in auction_products:
        all_sell_orders.extend(orders_provider.sell_orders(windows, product))
        all_buy_orders.extend(orders_provider.buy_orders(windows, product))

    target_window = my_window or windows[0]
    hypothetical_order_id = "HYPOTHETICAL_BID"
    hypothetical = SellOrderInput(
        order_id=hypothetical_order_id,
        auction_unit=my_auction_unit,
        basket_id=hypothetical_order_id,
        order_type="PARENT",
        auction_product=my_product,
        quantity_mw=my_quantity_mw,
        price_limit=my_price,
        window_start=target_window[0],
        window_end=target_window[1],
    )
    all_sell_orders.append(hypothetical)

    result = clear_auction(all_sell_orders, all_buy_orders)
    result.hypothetical_bid_order_id = hypothetical_order_id
    return result
