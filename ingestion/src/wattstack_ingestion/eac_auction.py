"""Real EAC (Enduring Auction Capability) clearing simulation --
reconstructs the actual competitive landscape for a real, historical
DC auction and determines whether a hypothetical additional sell order
(a BESS's own bid) would have been accepted, and at what clearing price.

Built from NESO's own N-SIDE Power Matching Algorithm public
description (https://www.neso.energy/document/282401/download,
June 2024) -- a genuine welfare-maximising Mixed Integer Linear
Program, not a simple merit-order stack. Section references below are
to that document, re-read directly (not from memory) when this module
was last changed; see docs/adr/0032 for what that re-reading corrected.

Two stages, in the order the document gives them (Section 7, 7.2,
Appendix A.1: the price problem is solved "after fixing the acceptance
ratio of orders via welfare maximisation"):

STAGE 1 -- acceptance (one joint MILP across every window supplied):
  - Multi-product co-optimisation (a unit's own baskets on overlapping
    windows are mutually exclusive -- Section 6.2 rule 6).
  - Parent orders (binary, rule 1), child orders (continuous, rule 2),
    substitutable child families (sum <= 1, rule 3), children only if
    their parent is accepted (rule 4).
  - Looped baskets: connected components of `looped_basket_id` links,
    equal acceptance across the family (rule 5).
  - Buy-side substitutability families (sum <= 1).
  - Volume balance per (product, window): accepted sell == accepted buy.
  - Rule 7 (child order surplus must be non-negative) and rule 8 (the
    total surplus of all accepted orders in a basket, or in a whole
    looped family, must be non-negative -- which is what lets a
    loss-making parent be accepted when its children, or its looped
    partners, carry surplus to it). Surplus transfers flow child ->
    parent and across looped baskets, which is all Section 7.1.1
    describes; parent -> child transfers are NOT modelled because the
    document does not say they exist.
  - Paradoxical acceptance of buy orders (Section 5.1, 5.3): a buy
    order accepted while the clearing price exceeds its limit price.
    `OverholdingMode.RESTRICTED` allows at most one such order per
    (product, window) (item 1); `OverholdingMode.PERMISSIVE` allows
    any number (item 2, "a more relaxed optimisation problem ...
    solutions of higher welfare"). Each buy order also carries the
    document's "Paradoxical acceptance allowed" flag (default True).
    Overholding = a zero-priced buy order (`overholding_buy_order()`),
    whose acceptance is paradoxical whenever the clearing price > 0.

  Rules 7/8 and the paradox cap all depend on the clearing price, so a
  price-aware Stage 1 is unavoidable. The naive encoding multiplies a
  price variable by a fractional acceptance ratio (bilinear), and the
  usual convex relaxation of that product can accept patterns that no
  price can support. This module avoids the relaxation entirely:
  sell-side rules only put LOWER bounds on a window's price and
  non-paradoxical accepted buy orders only put UPPER bounds on it, so
  a supporting price exists if and only if the HIGHEST permitted price
  satisfies every sell-side rule. That highest permitted price can
  only be one of a finite set (the limit price of some buy order that
  could bound it, or the price cap), so choosing it is a linear
  selection among binaries and the whole problem stays an exact MILP.
  Buy-order limit prices therefore decide which acceptances are
  PRICEABLE, but never the price itself -- consistent with Section
  7.1.2 ("buy orders' limit prices have no impact on the computation
  of the market clearing prices").

STAGE 2 -- Cost Minimisation Pricing (Section 7, 7.1.1, 7.2):
  With acceptance ratios fixed, one uniform price per (product,
  window) is chosen to MINIMISE procurement cost (sum of accepted sell
  volume x price), subject to: no accepted order paradoxically
  accepted (rules 7 and 8 above, evaluated at the actual price), hard
  upper bounds from buy orders that forbid paradoxical acceptance, the
  overholding cap (on the actual prices) and the price bounds. The
  acceptance ratios are constants here, so every surplus constraint is
  linear in the prices: an exact LP, plus one binary per accepted buy
  order when the RESTRICTED cap applies. Every result is re-checked by `verify_clearing()`'s
  independent rule checker before it is returned; a violation raises
  `PricingInfeasibleError` rather than returning a wrong answer.

What is deliberately NOT modelled, named directly:
  - Parent -> child surplus transfers, and surplus transfers inside a
    buy substitutability family: the document's glossary mentions the
    latter but gives no mechanics (and 7.1.1 describes transfers only
    toward the parent and across looped orders).
  - Rounding of clearing prices to two decimals (Section 7.3 mentions
    unrounded and rounded prices).
  - Explicit restrictions on PARADOXICAL REJECTION (Section 7.1.1
    lists the cases where it is permitted). Welfare maximisation, not
    a separate rule, decides rejections here.
  - Residual price indeterminacy. Section 7.2 itself notes cost
    minimisation "may not remove all price indeterminacy" (e.g. a
    looped family spanning windows can split a required total across
    the windows in several ways with identical cost). The solver
    returns one optimal vertex; `procurement_cost` is the well-defined
    quantity, the split is not.
  - Welfare ties: when two acceptance sets have identical welfare the
    solver picks one arbitrarily, so a hypothetical bid exactly on a
    knife-edge can flip between runs of different solver versions.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pulp

from wattstack_ingestion.neso import KNOWN_RESOURCES, NesoClient

Key = tuple[str, datetime, datetime]

_EPS = 1e-6  # noise threshold for acceptance ratios / volumes / prices


class OverholdingMode(str, Enum):
    """Section 5.3: how many buy orders may be paradoxically accepted
    (accepted while the clearing price exceeds their limit price) per
    (product, window)."""

    RESTRICTED = "restricted"  # item 1: at most one
    PERMISSIVE = "permissive"  # item 2: any number


class PricingInfeasibleError(RuntimeError):
    """Raised when the clearing result fails its own independent rule
    check, or Stage 2 cannot price the Stage 1 acceptance. By
    construction neither should happen; if one does it is a bug in
    this module and it must be loud, not a silently wrong price."""


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
    """`paradoxical_acceptance_allowed` is the document's Section 5.1
    Buyer parameter "Paradoxical acceptance allowed" (default True).
    Whether NESO's published buy-order dataset carries such a column
    has NOT been confirmed, so the provider below never sets it; it
    stays at the document's default."""

    order_id: str
    auction_product: str
    quantity_mw: float
    price: float  # £/MW/h
    window_start: datetime
    window_end: datetime
    substitutability_family: str | None = None
    paradoxical_acceptance_allowed: bool = True


@dataclass
class ClearingResult:
    """The outcome of clearing, potentially across multiple windows
    jointly.

    `hypothetical_bid_accepted`/`hypothetical_bid_acceptance_ratio`
    answer the actual question this module exists for.
    `clearing_price_by_product_window` is the Cost Minimisation Pricing
    result, keyed by (product, window_start, window_end). A window with
    no accepted sell volume has no determinable price and reports 0.0
    (clipped into the price bounds).

    Surplus figures are £/h: (price - limit) x MW x acceptance ratio.
    `individual_surplus_by_order_id` covers accepted sell orders; a
    parent's individual surplus can be negative while its basket's
    total (`basket_surplus_by_basket_id`) is not -- that is rule 8 at
    work. `looped_family_surplus` is keyed by the family's sorted
    basket IDs. `paradoxically_accepted_buy_order_ids` lists buy orders
    accepted while the clearing price exceeds their limit price, and
    `paradoxical_buy_volume_mw_by_product_window` is their accepted
    volume (the over-procurement, in the sense of Section 5.3).
    """

    acceptance_ratio_by_order_id: dict[str, float]
    clearing_price_by_product_window: dict[Key, float]
    hypothetical_bid_order_id: str | None = None
    buy_acceptance_ratio_by_order_id: dict[str, float] = field(default_factory=dict)
    paradoxically_accepted_buy_order_ids: list[str] = field(default_factory=list)
    paradoxical_buy_volume_mw_by_product_window: dict[Key, float] = field(default_factory=dict)
    individual_surplus_by_order_id: dict[str, float] = field(default_factory=dict)
    basket_surplus_by_basket_id: dict[str, float] = field(default_factory=dict)
    looped_family_surplus: dict[tuple[str, ...], float] = field(default_factory=dict)
    welfare: float = 0.0
    procurement_cost: float = 0.0
    overholding_mode: OverholdingMode = OverholdingMode.RESTRICTED
    price_bounds: tuple[float, float] | None = None

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
    """The synthetic overholding buy order of Section 5.3: price 0
    £/MW/h (so it never changes the welfare objective) and volume equal
    to the maximum the buyer agrees to over-procure.

    Its acceptance is "paradoxical" whenever the clearing price is
    above 0, so how many of them can be used at once is exactly what
    `OverholdingMode` controls:
      - RESTRICTED (item 1): one overholding order per (product,
        window) is the design; at most one paradoxically accepted buy
        order of any kind is allowed.
      - PERMISSIVE (item 2): several may be used together.
    With a NEGATIVE clearing price a zero-priced order is not
    paradoxical at all (the price is below its limit).

    Not always necessary to call this directly: if NESO's own
    historical buy orders already include a zero-priced order for a
    given product/window, fetching real data already captures it --
    this helper is for constructing a hypothetical scenario.
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



# ---------------------------------------------------------------------------
# Structure, bounds and validation
# ---------------------------------------------------------------------------

_VALID_ORDER_TYPES = {"PARENT", "CHILD", "SUBSTITUTABLECHILD"}


def _sell_key(so: SellOrderInput) -> Key:
    return (so.auction_product, so.window_start, so.window_end)


def _buy_key(bo: BuyOrderInput) -> Key:
    return (bo.auction_product, bo.window_start, bo.window_end)


@dataclass
class _Structure:
    baskets: dict[str, list[SellOrderInput]]
    parent: dict[str, SellOrderInput]
    families: list[list[str]]  # looped families (>= 2 baskets present in the data), sorted basket IDs
    groups: list[list[SellOrderInput]]  # rule-8 groups: a whole looped family, or a standalone basket
    group_baskets: list[tuple[str, ...]]  # basket IDs behind each group, same order as `groups`


def _build_structure(sell_orders: list[SellOrderInput]) -> _Structure:
    seen: set[str] = set()
    for so in sell_orders:
        if so.order_type not in _VALID_ORDER_TYPES:
            raise ValueError(f"sell order {so.order_id!r} has unknown order_type {so.order_type!r}")
        if so.order_id in seen:
            raise ValueError(f"duplicate sell order_id {so.order_id!r}")
        seen.add(so.order_id)

    baskets: dict[str, list[SellOrderInput]] = {}
    for so in sell_orders:
        baskets.setdefault(so.basket_id, []).append(so)

    parent: dict[str, SellOrderInput] = {}
    for basket_id, orders in baskets.items():
        parents = [o for o in orders if o.order_type == "PARENT"]
        if len(parents) != 1:
            # A child without its parent cannot be accepted (rule 4) and
            # its basket surplus (rule 8) is undefined -- typically a
            # truncated fetch. Loud, not silently wrong.
            raise ValueError(
                f"basket {basket_id!r} has {len(parents)} PARENT orders (expected exactly 1) -- "
                "a truncated fetch or malformed data; refusing to clear a partial basket"
            )
        parent[basket_id] = parents[0]

    families: list[list[str]] = []
    in_family: set[str] = set()
    for family in _looped_families(sell_orders):
        present = sorted(b for b in family if b in baskets)
        if len(present) >= 2:
            families.append(present)
            in_family.update(present)

    groups: list[list[SellOrderInput]] = []
    group_baskets: list[tuple[str, ...]] = []
    for family in families:
        groups.append([o for b in family for o in baskets[b]])
        group_baskets.append(tuple(family))
    for basket_id, orders in baskets.items():
        if basket_id not in in_family:
            groups.append(list(orders))
            group_baskets.append((basket_id,))

    return _Structure(baskets, parent, families, groups, group_baskets)


def _validate_buy_orders(buy_orders: list[BuyOrderInput]) -> None:
    seen: set[str] = set()
    for bo in buy_orders:
        if bo.order_id in seen:
            raise ValueError(f"duplicate buy order_id {bo.order_id!r}")
        seen.add(bo.order_id)


def _resolve_bounds(
    sell_orders: list[SellOrderInput], buy_orders: list[BuyOrderInput], price_bounds: tuple[float, float] | None
) -> tuple[float, float]:
    """Section 7.3: the same minimum/maximum applies to every product
    and window, and every bid price must lie within it. When not given,
    the bounds are derived from the data as [min(0, lowest order price),
    max(0, highest order price)] -- wide enough never to exclude a real
    bid, and 0 (not an arbitrary number) as the report value for a
    window with no accepted volume."""
    prices = [so.price_limit for so in sell_orders] + [bo.price for bo in buy_orders]
    if price_bounds is None:
        return min([0.0] + prices), max([0.0] + prices)
    lo, hi = price_bounds
    if lo > hi:
        raise ValueError(f"price_bounds minimum {lo} exceeds maximum {hi}")
    outside = [p for p in prices if p < lo - 1e-12 or p > hi + 1e-12]
    if outside:
        raise ValueError(f"order prices {sorted(set(outside))} lie outside price_bounds {(lo, hi)} (Section 7.3)")
    return lo, hi


def _clean(value: float | None) -> float:
    v = float(value or 0.0)
    if abs(v) < _EPS:
        return 0.0
    if abs(v - 1.0) < _EPS:
        return 1.0
    return v


# ---------------------------------------------------------------------------
# Stage 1 + Stage 2
# ---------------------------------------------------------------------------


def clear_auction(
    sell_orders: list[SellOrderInput],
    buy_orders: list[BuyOrderInput],
    *,
    overholding_mode: OverholdingMode = OverholdingMode.RESTRICTED,
    price_bounds: tuple[float, float] | None = None,
) -> ClearingResult:
    """Clears every window present in the orders jointly: Stage 1
    (price-aware acceptance MILP) then Stage 2 (Cost Minimisation
    Pricing LP). See this module's docstring for exactly which rules
    are enforced and how.

    `overholding_mode` defaults to RESTRICTED (Section 5.3 item 1, at
    most one paradoxically accepted buy order per product/window).
    Earlier versions (ADR 0030/0031) had no cap at all and behaved as
    an unbounded permissive model while documenting themselves as
    restricted; PERMISSIVE now reproduces "no cap" deliberately.

    Mutual exclusivity is scoped to a single unit's own baskets on
    OVERLAPPING windows, not market-wide -- confirmed from the design
    document's co-optimisation example (Section 4, Figure 2).
    """
    overholding_mode = OverholdingMode(overholding_mode)
    struct = _build_structure(sell_orders)
    _validate_buy_orders(buy_orders)
    lo, hi = _resolve_bounds(sell_orders, buy_orders, price_bounds)

    keys = sorted({_sell_key(so) for so in sell_orders} | {_buy_key(bo) for bo in buy_orders})
    if not keys:
        return ClearingResult(
            acceptance_ratio_by_order_id={}, clearing_price_by_product_window={},
            overholding_mode=overholding_mode, price_bounds=(lo, hi),
        )
    key_index = {k: i for i, k in enumerate(keys)}
    sells_by_key: dict[Key, list[SellOrderInput]] = defaultdict(list)
    for so in sell_orders:
        sells_by_key[_sell_key(so)].append(so)
    buys_by_key: dict[Key, list[BuyOrderInput]] = defaultdict(list)
    for bo in buy_orders:
        buys_by_key[_buy_key(bo)].append(bo)

    prob = pulp.LpProblem("eac_clearing", pulp.LpMaximize)

    # --- acceptance variables ---
    x: dict[str, pulp.LpVariable] = {}
    for i, so in enumerate(sell_orders):
        if so.order_type == "PARENT":
            x[so.order_id] = pulp.LpVariable(f"x_{i}", cat=pulp.LpBinary)
        else:
            x[so.order_id] = pulp.LpVariable(f"x_{i}", lowBound=0, upBound=1)
    y = {bo.order_id: pulp.LpVariable(f"y_{j}", lowBound=0, upBound=1) for j, bo in enumerate(buy_orders)}

    # --- welfare objective (Section 3) ---
    prob += (
        pulp.lpSum(y[bo.order_id] * bo.quantity_mw * bo.price for bo in buy_orders)
        - pulp.lpSum(x[so.order_id] * so.quantity_mw * so.price_limit for so in sell_orders)
    )

    # --- rules 3, 4: children and substitutable families ---
    basket_x = {basket_id: x[p.order_id] for basket_id, p in struct.parent.items()}
    for basket_id, orders in struct.baskets.items():
        for so in orders:
            if so.order_type != "PARENT":
                prob += x[so.order_id] <= basket_x[basket_id]
        substitutable = [so for so in orders if so.order_type == "SUBSTITUTABLECHILD"]
        if substitutable:
            prob += pulp.lpSum(x[so.order_id] for so in substitutable) <= 1

    # --- rule 6: mutual exclusivity, per unit, on overlapping windows ---
    unit_baskets: dict[str, list[str]] = {}
    for basket_id, p in struct.parent.items():
        unit_baskets.setdefault(p.auction_unit, []).append(basket_id)
    for basket_ids in unit_baskets.values():
        for i in range(len(basket_ids)):
            for j in range(i + 1, len(basket_ids)):
                b1, b2 = basket_ids[i], basket_ids[j]
                p1, p2 = struct.parent[b1], struct.parent[b2]
                if _windows_overlap(p1.window_start, p1.window_end, p2.window_start, p2.window_end):
                    prob += basket_x[b1] + basket_x[b2] <= 1

    # --- rule 5: looped families clear together or not at all ---
    for family in struct.families:
        for other in family[1:]:
            prob += basket_x[family[0]] == basket_x[other]

    # --- buy-side substitutability ---
    buy_families: dict[str, list[BuyOrderInput]] = {}
    for bo in buy_orders:
        if bo.substitutability_family:
            buy_families.setdefault(bo.substitutability_family, []).append(bo)
    for orders_in_family in buy_families.values():
        prob += pulp.lpSum(y[bo.order_id] for bo in orders_in_family) <= 1

    # --- volume balance per (product, window) ---
    # Equality, not <=: "<=" lets the solver collect buy-side utility
    # with zero matching sell volume (the bug recorded in ADR 0030).
    # Overholding does not relax this equality; it adds a zero-priced
    # buy order to the buy side.
    for k in keys:
        prob += (
            pulp.lpSum(x[so.order_id] * so.quantity_mw for so in sells_by_key[k])
            == pulp.lpSum(y[bo.order_id] * bo.quantity_mw for bo in buys_by_key[k])
        )

    # --- price layer: rules 7, 8 and the paradox cap, exact ---
    # Per (product, window) a binary `s` selects the HIGHEST permitted
    # price level U_k (see module docstring for why that is sufficient).
    def _can_bound_price(bo: BuyOrderInput) -> bool:
        if bo.price >= hi:
            return False  # price <= hi <= bo.price, never paradoxical, never binding
        return (not bo.paradoxical_acceptance_allowed) or overholding_mode is OverholdingMode.RESTRICTED

    levels: dict[Key, list[float]] = {}
    for k in keys:
        candidate_prices = {hi}
        candidate_prices.update(bo.price for bo in buys_by_key[k] if _can_bound_price(bo))
        levels[k] = sorted(candidate_prices)

    s: dict[tuple[Key, int], pulp.LpVariable] = {}
    for k in keys:
        for m in range(len(levels[k])):
            s[(k, m)] = pulp.LpVariable(f"s_{key_index[k]}_{m}", cat=pulp.LpBinary)
        prob += pulp.lpSum(s[(k, m)] for m in range(len(levels[k]))) == 1

    def _levels_below(k: Key, value: float) -> list[int]:
        return [m for m, beta in enumerate(levels[k]) if beta < value - 1e-12]

    # Rule 7: an accepted child needs a price level >= its limit.
    for i, so in enumerate(sell_orders):
        if so.order_type == "PARENT":
            continue
        below = _levels_below(_sell_key(so), so.price_limit)
        if not below:
            continue
        k = _sell_key(so)
        z = pulp.LpVariable(f"z_{i}", cat=pulp.LpBinary)  # z = 1 "child is in use"
        prob += x[so.order_id] <= z
        prob += pulp.lpSum(s[(k, m)] for m in below) <= 1 - z

    # Rule 8: total surplus of every accepted group (standalone basket,
    # or a whole looped family) >= 0 at the permitted price level.
    for gi, group in enumerate(struct.groups):
        if len(group) == 1:
            # A lone parent: total surplus is its own, so it needs level >= limit.
            only = group[0]
            below = _levels_below(_sell_key(only), only.price_limit)
            if below:
                k = _sell_key(only)
                prob += pulp.lpSum(s[(k, m)] for m in below) <= 1 - x[only.order_id]
            continue
        by_key: dict[Key, list[SellOrderInput]] = defaultdict(list)
        for so in group:
            by_key[_sell_key(so)].append(so)
        priced_volume_terms = []
        for k, orders_k in by_key.items():
            q_max = sum(o.quantity_mw for o in orders_k)
            level_volumes = []
            for m, beta in enumerate(levels[k]):
                v = pulp.LpVariable(f"v_{gi}_{key_index[k]}_{m}", lowBound=0)
                prob += v <= q_max * s[(k, m)]
                level_volumes.append(v)
                priced_volume_terms.append(beta * v)
            # The group's accepted volume in this window is attributed
            # entirely to whichever level is selected (all others are 0).
            prob += pulp.lpSum(level_volumes) == pulp.lpSum(x[o.order_id] * o.quantity_mw for o in orders_k)
        prob += (
            pulp.lpSum(priced_volume_terms)
            >= pulp.lpSum(x[o.order_id] * o.quantity_mw * o.price_limit for o in group)
        )

    # Buy side (Section 5): an accepted buy order that is not
    # paradoxically accepted needs level <= its limit price.
    paradox_flags: dict[Key, list[pulp.LpVariable]] = defaultdict(list)
    for j, bo in enumerate(buy_orders):
        if not _can_bound_price(bo):
            continue
        k = _buy_key(bo)
        above = [m for m, beta in enumerate(levels[k]) if beta > bo.price + 1e-12]
        a = pulp.LpVariable(f"a_{j}", cat=pulp.LpBinary)  # a = 1 "order is in use"
        prob += y[bo.order_id] <= a
        if bo.paradoxical_acceptance_allowed:
            pa = pulp.LpVariable(f"pa_{j}", cat=pulp.LpBinary)  # pa = 1 "used paradoxically"
            prob += pulp.lpSum(s[(k, m)] for m in above) <= 1 - a + pa
            paradox_flags[k].append(pa)
        else:
            prob += pulp.lpSum(s[(k, m)] for m in above) <= 1 - a
    if overholding_mode is OverholdingMode.RESTRICTED:
        for flags in paradox_flags.values():
            prob += pulp.lpSum(flags) <= 1

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"EAC clearing MILP did not find an optimal solution: {pulp.LpStatus[prob.status]}")

    x_val = {so.order_id: _clean(pulp.value(x[so.order_id])) for so in sell_orders}
    y_val = {bo.order_id: _clean(pulp.value(y[bo.order_id])) for bo in buy_orders}

    prices = _price_stage(sell_orders, buy_orders, struct, x_val, y_val, keys, lo, hi, overholding_mode)

    violations = _find_violations(sell_orders, buy_orders, struct, x_val, y_val, prices, overholding_mode, lo, hi, _EPS)
    if violations:
        raise PricingInfeasibleError("clearing result failed its own rule check: " + "; ".join(violations))

    return _assemble_result(
        sell_orders, buy_orders, struct, x_val, y_val, prices, overholding_mode, (lo, hi)
    )


def _price_stage(
    sell_orders: list[SellOrderInput],
    buy_orders: list[BuyOrderInput],
    struct: _Structure,
    x: dict[str, float],
    y: dict[str, float],
    keys: list[Key],
    lo: float,
    hi: float,
    mode: OverholdingMode,
) -> dict[Key, float]:
    """Stage 2, Cost Minimisation Pricing (Section 7.2): with the
    acceptance ratios fixed, choose one price per (product, window)
    minimising sum(accepted sell volume x price) subject to Section
    7.1.1 (no accepted order paradoxically accepted), the buyers' own
    paradox flags, the overholding cap, and the bounds.

    The sell-side rules are linear because acceptance ratios are
    constants. In RESTRICTED mode the cap (at most one paradoxically
    accepted buy order per window) is enforced on the ACTUAL prices with
    one binary per accepted buy order, so this is a small MILP then (an
    LP in PERMISSIVE mode). The cap must be enforced here and not only in
    Stage 1: in a looped family the cheapest way to meet the family's
    required total can be to raise one window's price above the level
    Stage 1 relied on, which would add paradoxical buyers there (found by
    stress test; see docs/adr/0032)."""
    accepted = [so for so in sell_orders if x[so.order_id] > _EPS]
    volume: dict[Key, float] = defaultdict(float)
    for so in accepted:
        volume[_sell_key(so)] += so.quantity_mw * x[so.order_id]
    active = [k for k in keys if volume.get(k, 0.0) > _EPS]

    prices: dict[Key, float] = {k: min(max(0.0, lo), hi) for k in keys}
    if not active:
        return prices

    prob = pulp.LpProblem("eac_pricing", pulp.LpMinimize)
    pi = {k: pulp.LpVariable(f"pi_{i}", lowBound=lo, upBound=hi) for i, k in enumerate(active)}
    prob += pulp.lpSum(volume[k] * pi[k] for k in active)

    for so in accepted:  # rule 7
        if so.order_type != "PARENT":
            prob += pi[_sell_key(so)] >= so.price_limit

    for group in struct.groups:  # rule 8: group total surplus >= 0
        coefficients: dict[Key, float] = defaultdict(float)
        cost = 0.0
        for so in group:
            ratio = x[so.order_id]
            if ratio > _EPS:
                coefficients[_sell_key(so)] += so.quantity_mw * ratio
                cost += so.quantity_mw * ratio * so.price_limit
        if coefficients:
            prob += pulp.lpSum(c * pi[k] for k, c in coefficients.items()) >= cost

    for bo in buy_orders:  # buyers that forbid paradoxical acceptance
        k = _buy_key(bo)
        if y[bo.order_id] > _EPS and not bo.paradoxical_acceptance_allowed and k in pi:
            prob += pi[k] <= bo.price

    if mode is OverholdingMode.RESTRICTED:  # the cap, on the actual prices
        paradox_flags: dict[Key, list[pulp.LpVariable]] = defaultdict(list)
        for j, bo in enumerate(buy_orders):
            k = _buy_key(bo)
            if y[bo.order_id] > _EPS and bo.paradoxical_acceptance_allowed and bo.price < hi and k in pi:
                pa = pulp.LpVariable(f"pa_{j}", cat=pulp.LpBinary)  # pa = 1 "accepted paradoxically"
                prob += pi[k] <= bo.price + (hi - bo.price) * pa
                paradox_flags[k].append(pa)
        for flags in paradox_flags.values():
            prob += pulp.lpSum(flags) <= 1

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise PricingInfeasibleError(
            f"Stage 2 could not price the Stage 1 acceptance ({pulp.LpStatus[prob.status]}); "
            "Stage 1 guarantees a supporting price exists, so this is a bug in the formulation"
        )
    for k in active:
        prices[k] = round(float(pulp.value(pi[k])), 6)
    return prices


def _paradoxical_buy_ids(
    buy_orders: list[BuyOrderInput], y: dict[str, float], prices: dict[Key, float], tol: float
) -> list[str]:
    return [
        bo.order_id for bo in buy_orders
        if y[bo.order_id] > tol and prices.get(_buy_key(bo), 0.0) > bo.price + tol
    ]


def _assemble_result(
    sell_orders: list[SellOrderInput],
    buy_orders: list[BuyOrderInput],
    struct: _Structure,
    x: dict[str, float],
    y: dict[str, float],
    prices: dict[Key, float],
    mode: OverholdingMode,
    bounds: tuple[float, float],
) -> ClearingResult:
    individual: dict[str, float] = {}
    for so in sell_orders:
        if x[so.order_id] > _EPS:
            individual[so.order_id] = (prices[_sell_key(so)] - so.price_limit) * so.quantity_mw * x[so.order_id]

    basket_surplus: dict[str, float] = {}
    for basket_id, orders in struct.baskets.items():
        if x[struct.parent[basket_id].order_id] > _EPS:
            basket_surplus[basket_id] = sum(individual.get(o.order_id, 0.0) for o in orders)

    family_surplus: dict[tuple[str, ...], float] = {}
    for family in struct.families:
        if x[struct.parent[family[0]].order_id] > _EPS:
            family_surplus[tuple(family)] = sum(
                individual.get(o.order_id, 0.0) for b in family for o in struct.baskets[b]
            )

    paradoxical_ids = _paradoxical_buy_ids(buy_orders, y, prices, _EPS)
    paradoxical_volume: dict[Key, float] = defaultdict(float)
    by_id = {bo.order_id: bo for bo in buy_orders}
    for order_id in paradoxical_ids:
        bo = by_id[order_id]
        paradoxical_volume[_buy_key(bo)] += bo.quantity_mw * y[order_id]

    welfare = (
        sum(bo.quantity_mw * bo.price * y[bo.order_id] for bo in buy_orders)
        - sum(so.quantity_mw * so.price_limit * x[so.order_id] for so in sell_orders)
    )
    procurement = sum(so.quantity_mw * x[so.order_id] * prices[_sell_key(so)] for so in sell_orders)

    return ClearingResult(
        acceptance_ratio_by_order_id={oid: round(v, 4) for oid, v in x.items()},
        clearing_price_by_product_window=dict(prices),
        buy_acceptance_ratio_by_order_id={oid: round(v, 4) for oid, v in y.items()},
        paradoxically_accepted_buy_order_ids=paradoxical_ids,
        paradoxical_buy_volume_mw_by_product_window=dict(paradoxical_volume),
        individual_surplus_by_order_id=individual,
        basket_surplus_by_basket_id=basket_surplus,
        looped_family_surplus=family_surplus,
        welfare=welfare,
        procurement_cost=procurement,
        overholding_mode=mode,
        price_bounds=bounds,
    )


# ---------------------------------------------------------------------------
# Independent rule checker
# ---------------------------------------------------------------------------


def _find_violations(
    sell_orders: list[SellOrderInput],
    buy_orders: list[BuyOrderInput],
    struct: _Structure,
    x: dict[str, float],
    y: dict[str, float],
    prices: dict[Key, float],
    mode: OverholdingMode,
    lo: float,
    hi: float,
    tol: float,
) -> list[str]:
    """Re-derives every rule from the raw orders, ratios and prices,
    sharing no code path with the MILP formulation -- so a formulation
    bug (a wrong big-M, a missing constraint) shows up here instead of
    as a plausible wrong answer."""
    out: list[str] = []
    value_scale = 1.0 + sum(so.quantity_mw for so in sell_orders) * max(1.0, hi - lo)

    for so in sell_orders:
        r = x[so.order_id]
        if r < -tol or r > 1 + tol:
            out.append(f"sell {so.order_id}: ratio {r} outside [0,1]")
        if so.order_type == "PARENT" and min(abs(r), abs(1 - r)) > tol:
            out.append(f"rule 1: parent {so.order_id} has fractional ratio {r}")
    for bo in buy_orders:
        r = y[bo.order_id]
        if r < -tol or r > 1 + tol:
            out.append(f"buy {bo.order_id}: ratio {r} outside [0,1]")

    for basket_id, orders in struct.baskets.items():  # rules 3, 4
        parent_ratio = x[struct.parent[basket_id].order_id]
        for so in orders:
            if so.order_type != "PARENT" and x[so.order_id] > parent_ratio + tol:
                out.append(f"rule 4: child {so.order_id} accepted beyond its parent")
        subs = sum(x[so.order_id] for so in orders if so.order_type == "SUBSTITUTABLECHILD")
        if subs > 1 + tol:
            out.append(f"rule 3: substitutable family of basket {basket_id} sums to {subs}")

    for family in struct.families:  # rule 5
        ratios = [x[struct.parent[b].order_id] for b in family]
        if max(ratios) - min(ratios) > tol:
            out.append(f"rule 5: looped baskets {family} have unequal acceptance {ratios}")

    parents = list(struct.parent.values())  # rule 6
    for i in range(len(parents)):
        for j in range(i + 1, len(parents)):
            p1, p2 = parents[i], parents[j]
            if p1.auction_unit == p2.auction_unit and _windows_overlap(
                p1.window_start, p1.window_end, p2.window_start, p2.window_end
            ):
                if x[p1.order_id] + x[p2.order_id] > 1 + tol:
                    out.append(f"rule 6: unit {p1.auction_unit} accepted both {p1.basket_id} and {p2.basket_id}")

    buy_families: dict[str, float] = defaultdict(float)
    for bo in buy_orders:
        if bo.substitutability_family:
            buy_families[bo.substitutability_family] += y[bo.order_id]
    for family_id, total in buy_families.items():
        if total > 1 + tol:
            out.append(f"buy family {family_id} sums to {total}")

    keys = {_sell_key(so) for so in sell_orders} | {_buy_key(bo) for bo in buy_orders}
    for k in keys:  # volume balance
        sell_volume = sum(so.quantity_mw * x[so.order_id] for so in sell_orders if _sell_key(so) == k)
        buy_volume = sum(bo.quantity_mw * y[bo.order_id] for bo in buy_orders if _buy_key(bo) == k)
        if abs(sell_volume - buy_volume) > tol * (1.0 + max(sell_volume, buy_volume)):
            out.append(f"volume balance {k[0]}: sell {sell_volume} != buy {buy_volume}")
        price = prices.get(k, 0.0)
        if price < lo - tol or price > hi + tol:
            out.append(f"price {price} for {k[0]} outside bounds {(lo, hi)}")

    for so in sell_orders:  # rule 7
        if so.order_type != "PARENT" and x[so.order_id] > tol:
            if prices.get(_sell_key(so), 0.0) < so.price_limit - tol:
                out.append(f"rule 7: child {so.order_id} accepted with negative surplus")

    for group, label in zip(struct.groups, struct.group_baskets):  # rule 8
        total = sum(
            (prices.get(_sell_key(so), 0.0) - so.price_limit) * so.quantity_mw * x[so.order_id]
            for so in group if x[so.order_id] > tol
        )
        if total < -tol * value_scale:
            out.append(f"rule 8: baskets {label} accepted with total surplus {total}")

    paradoxical: dict[Key, list[str]] = defaultdict(list)
    for bo in buy_orders:  # buy side paradox
        if y[bo.order_id] > tol and prices.get(_buy_key(bo), 0.0) > bo.price + tol:
            if not bo.paradoxical_acceptance_allowed:
                out.append(f"buy {bo.order_id}: paradoxically accepted but the buyer forbids it")
            paradoxical[_buy_key(bo)].append(bo.order_id)
    if mode is OverholdingMode.RESTRICTED:
        for k, ids in paradoxical.items():
            if len(ids) > 1:
                out.append(f"restricted overholding: {len(ids)} paradoxically accepted buy orders in {k[0]}: {ids}")
    return out


def verify_clearing(
    sell_orders: list[SellOrderInput],
    buy_orders: list[BuyOrderInput],
    result: ClearingResult,
    overholding_mode: OverholdingMode | None = None,
    price_bounds: tuple[float, float] | None = None,
    tolerance: float = 1e-3,
) -> list[str]:
    """Independently re-checks a ClearingResult against the design
    document's rules (1-8, volume balance, price bounds, the paradox
    flag and the overholding cap). Returns human-readable violations;
    an empty list means every rule holds. `tolerance` is looser than the
    solver's own (1e-3) because a ClearingResult stores ratios rounded
    to 4 decimals. `overholding_mode`/`price_bounds` default to what the
    result was produced with."""
    struct = _build_structure(sell_orders)
    mode = OverholdingMode(overholding_mode) if overholding_mode is not None else result.overholding_mode
    if price_bounds is not None:
        lo, hi = _resolve_bounds(sell_orders, buy_orders, price_bounds)
    elif result.price_bounds is not None:
        lo, hi = result.price_bounds
    else:
        lo, hi = _resolve_bounds(sell_orders, buy_orders, None)
    x = {so.order_id: result.acceptance_ratio_by_order_id.get(so.order_id, 0.0) for so in sell_orders}
    y = {bo.order_id: result.buy_acceptance_ratio_by_order_id.get(bo.order_id, 0.0) for bo in buy_orders}
    return _find_violations(
        sell_orders, buy_orders, struct, x, y, result.clearing_price_by_product_window, mode, lo, hi, tolerance
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
    overholding_mode: OverholdingMode = OverholdingMode.RESTRICTED,
    price_bounds: tuple[float, float] | None = None,
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
    `windows[0]` if not given), and clears the joint auction.

    `overholding_mode` and `price_bounds` pass straight through to
    `clear_auction()`. Running a backtest under both modes and comparing
    with what actually happened is the way to learn which Section 5.3
    version NESO's live auction behaves like -- the design document
    does not say which one is in force.

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

    result = clear_auction(
        all_sell_orders, all_buy_orders, overholding_mode=overholding_mode, price_bounds=price_bounds
    )
    result.hypothetical_bid_order_id = hypothetical_order_id
    return result
