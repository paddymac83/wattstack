# 30. EAC auction clearing simulation: a genuine co-optimising MILP, and a real formulation bug found and fixed

Date: 2026-08-21

## Status
Accepted -- implemented (Stage 1, acceptance). Stage 2 (pricing) is a
stated approximation, not a replica -- see Consequences.

## Context
The user asked to test their DC bid/offer pricing against realistic
buy and sell curves -- specifically wanting to determine (a) whether a
hypothetical bid would have been accepted in the real EAC auction, and
(b) how marginal cost assumptions affect that acceptance probability,
backtested against real historical data. Given a direct choice between
a simplified single-product merit-order approximation and modelling
real co-optimisation effects (a unit competing across products,
baskets, mutual exclusivity, parent/child structure), the user chose
the latter explicitly: fidelity over simplicity.

Two real sources grounded this work, both fetched and read directly,
not assumed:
- NESO's own N-SIDE Power Matching Algorithm public description
  (https://www.neso.energy/document/282401/download, June 2024,
  33 pages) -- confirms the real clearing mechanism is a welfare-
  maximising Mixed Integer Linear Program, not a merit-order stack.
  Co-optimisation across products, baskets with mutual exclusivity,
  non-curtailable parent orders, curtailable child and substitutable
  child orders, looped baskets, buy-side substitutability, overholding
  via paradoxical acceptance, and "Cost Minimisation Pricing" (not
  simple marginal pricing) are all real, documented features.
- Both order-level datasets' own schema pages, fetched directly:
  sell orders (resource ID `8ba48f26-d73e-4094-a90d-ba075eb739c1`) and
  buy orders (`e638cf3d-bed1-42ae-a984-8fe3c556febf`), each confirmed
  via a "CKAN Data API" section showing real `datastore_search`
  examples -- the same confirmation evidence already trusted elsewhere
  in this project.

**A real discrepancy found and corrected before any new code was
written**: `KNOWN_RESOURCES` already had entries for these two
datasets, but with different resource IDs (`1cf68f59...`/`13b511df...`),
sourced from the general `eac-auction-results` listing page rather
than the datasets' own pages. That module's own docstring already
flagged these as unconfirmed for `datastore_search` (only plain CSV
download links were visible on the listing page). The freshly-fetched
dataset pages show clear datastore_search evidence the listing page
didn't. Corrected in place, not left as a second, competing pair of
IDs.

## Decision
- `eac_auction.py` (new module, `ingestion`): `SellOrderInput`/
  `BuyOrderInput` dataclasses whose field names deliberately match the
  confirmed CKAN schema directly (`auctionUnit`, `basketID`,
  `orderType`, `priceLimit`) rather than being renamed -- a raw row
  maps to a dataclass field by direct correspondence, not
  reinterpretation.
- `ResponseReserveOrdersProvider`: fetches real sell/buy orders for a
  given service window and product. NOT a `PriceProvider` -- doesn't
  implement `wholesale_prices()`/`reserve_prices()`, a genuinely
  different kind of data (order-level detail) for a genuinely
  different purpose (backtesting acceptance) than everything else in
  `prices.py`.
- `clear_auction()`: a real MILP via `pulp` (newly added as an
  `ingestion` dependency -- a third-party solver library, not
  `wattstack_core`, so this doesn't violate ingestion's "no dependency
  on core" rule). Models, faithfully:
  - Binary acceptance for PARENT orders (non-curtailable); continuous
    [0,1] for CHILD/SUBSTITUTABLECHILD orders (curtailable) and all
    buy orders (always fully curtailable, per the design document).
  - Parent-child linkage: a child's acceptance ratio is bounded by its
    basket's parent's own acceptance.
  - Substitutable-family constraint: the sum of acceptance ratios
    within a basket's substitutable children never exceeds 1.
  - **Mutual exclusivity scoped to a single unit's own baskets, not
    market-wide** -- confirmed directly from the design document's own
    co-optimisation example (Section 4, Figure 2): one Market
    Participant's two baskets (one per product) compete against each
    other, while a *different* unit is simultaneously and
    independently accepted alongside whichever of the first unit's
    baskets wins. Proven by test in both directions
    (`test_single_unit_two_baskets_only_one_accepted`,
    `test_different_units_are_not_mutually_exclusive`).
  - Objective: maximise (accepted buy utility) - (accepted sell cost),
    the welfare-maximisation principle stated directly in the design
    document's Section 3.
- `backtest_hypothetical_bid()`: the actual thing this exists for --
  fetches real orders across every product passed in (co-optimisation
  needs every product a unit might compete across, not just the one
  the hypothetical bid targets), inserts the hypothetical bid as its
  own standalone parent order, solves, and reports whether it would
  have cleared.

## A real formulation bug found and fixed, not a hypothetical edge case
The first version of the volume-balance constraint was
`accepted_sell <= accepted_buy`. This is wrong, and it's wrong in a way
that matters: it allows the solver to "accept" buy-side utility with
**zero matching sell volume** (`x=0, y=1` satisfies `0 <= 10`),
collecting welfare from a trade that never actually happened. Caught
immediately -- the very first, simplest possible test (one cheap sell
order, one buy order that clearly wants it) returned 0% acceptance for
an unambiguously profitable trade. Diagnosed by hand-reproducing the
exact MILP outside the module and reading the solver's own chosen
values (`x=0, y=1`, objective still positive) rather than guessing at
the cause. Fixed with equality (`accepted_sell == accepted_buy`) --
correct given overholding isn't modelled here (a real, deferred
feature, see below): without it, accepted buy and sell volume must
match exactly, not merely be bounded by each other. Buy orders are
always fully curtailable (continuous), so the buy side can always flex
to match whatever the (possibly binary-constrained) sell side actually
provides -- equality is always achievable, not an over-constraint.
Confirmed by re-running the same minimal reproduction after the fix
before touching the full test suite.

## Consequences
- What is NOT modelled, named directly rather than implied solved:
  - **Looped baskets** across non-overlapping service windows -- each
    service window is cleared independently here.
  - **Buy-side substitutability families** -- NESO's own indifference
    between products (Appendix B's worked examples in the design
    document) is not represented; each buy order here is independent.
  - **Overholding / paradoxical buy-order acceptance** -- sell volume
    is capped at exactly matching buy volume, never allowed to exceed
    it, even though the real system permits this via a specific
    zero-price overholding order.
  - **Stage 2 (pricing) is a stated approximation, not the real
    rule**: the real "Cost Minimisation Pricing" (Section 7) finds the
    lowest clearing price satisfying non-negative *basket-level*
    surplus (allowing transfers between orders in the same basket or
    looped family) via a separate optimisation. This module instead
    uses the price of the most expensive accepted order per product --
    simpler, and explicitly flagged in the code and this ADR as an
    approximation, not a replica.
- 19 new tests, all passing, covering each modelled rule individually
  (basic welfare maximisation, volume balance, per-unit mutual
  exclusivity in both directions, parent-child linkage, substitutable
  families, the full hypothetical-bid pipeline, and confirmed field
  mapping from real CKAN schema to the dataclasses) -- 343 total
  ingestion tests, no regressions.
- Not yet done: running this against real, live order data (the
  provider fetches real data, but hasn't been exercised against it in
  this session), and building the "vary marginal cost, observe
  acceptance probability" backtesting loop the user's original request
  named as the ultimate goal -- this ADR covers the clearing engine
  itself, not yet the sweep built on top of it.
