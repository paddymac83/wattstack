# 31. Full EAC fidelity: looped baskets, buy-side substitutability, overholding

Date: 2026-08-21

## Status
Accepted -- implemented

## Context
`docs/adr/0030` deliberately deferred three real EAC features rather
than build a partial version of each: looped baskets (linking baskets
across non-overlapping service windows so they clear together or not
at all), buy-side substitutability (NESO expressing indifference
between products, mirroring sell-side substitutable children), and
overholding (procuring more than nominal demand when a negatively-
priced sell order makes it welfare-positive to do so). The user chose
explicitly to model all three now, before any live testing or
backtesting sweeps -- fidelity over simplicity, continuing the same
choice made in ADR 0030.

A real architectural consequence, not an incremental add: looping and
buy-side substitutability both require clearing *multiple service
windows jointly*. The previous design cleared one window at a time,
with the window itself implicit (assumed shared across every order
passed in). A loop, by definition, links baskets on non-overlapping
windows -- there is no way to represent that constraint without
windows becoming explicit, per-order data, and `clear_auction()`
solving across all of them as one combined MILP.

## Decision
- `SellOrderInput`/`BuyOrderInput` both gain explicit `window_start`/
  `window_end` fields, plus `looped_basket_id` (sell) and
  `substitutability_family` (buy) -- both already-confirmed CKAN
  fields, not new inventions.
- Mutual exclusivity is now based on genuine window overlap
  (`start1 < end2 and start2 < end1`), scoped per unit as before --
  not an assumption that every basket in one call shares an implicit
  common window. Proven by test in both directions: two of a unit's
  own baskets on the *same* window are still mutually exclusive; two
  on genuinely non-overlapping windows are not
  (`test_single_unit_two_baskets_non_overlapping_windows_both_can_be_accepted`).
- `_looped_families()`: groups basket IDs into connected components
  via `looped_basket_id` links -- deliberately handles either a chain
  (A links to B, B links to C) or a star (B and C both link to A)
  representation correctly, since which one the real data actually
  uses wasn't confirmed and shouldn't need to be assumed. All baskets
  in a family are constrained to equal acceptance ratios.
- **Two hand-constructed test scenarios prove the loop constraint
  genuinely changes the outcome, not just that it doesn't crash**: one
  basket alone would clearly be accepted (+950 welfare) and its loop
  partner alone would clearly be rejected if forced (-1000 welfare) --
  looped together, the combined welfare (-50) is worse than rejecting
  both (0), so the loop correctly forces the otherwise-obviously-
  profitable basket to be rejected too
  (`test_looped_baskets_forced_to_reject_together_when_combined_welfare_negative`).
  The mirror case, where the combined loop stays net positive, shows
  both correctly accepted together
  (`test_looped_baskets_both_accepted_when_combined_welfare_positive`).
- Buy-side substitutability: `sum(buy_vars[j] for j in family) <= 1`,
  the direct mirror of the existing sell-side substitutable-child
  constraint. Proven with two individually-profitable sell orders
  whose matching buy orders share a family -- only one (or a
  combination summing to at most 1) can actually clear, even though
  both would clear independently without the family constraint.
- `overholding_buy_order()`: constructs the synthetic zero-price buy
  order the design document itself describes (Section 5.3.1, "at most
  one paradoxically accepted buy order") -- price 0, volume equal to
  the maximum over-procurement allowed. No special-cased acceptance
  logic needed in `clear_auction()` itself: a zero price contributes
  nothing to the objective directly, so welfare maximisation only
  uses this headroom when a negatively-priced sell order (explicitly
  allowed by the design document) makes doing so genuinely
  beneficial. Proven in both directions: a negatively-priced sell
  order does use the overholding headroom to clear fully
  (`test_overholding_buy_order_used_when_it_enables_a_negatively_priced_sell_order`);
  a positively-priced one does not go beyond real demand
  (`test_overholding_buy_order_unused_when_sell_order_priced_positively`).
- `ClearingResult.clearing_price_by_product_window` (renamed from
  `clearing_price_by_product`) is now keyed by `(product, window_start,
  window_end)`, not product alone -- a real, necessary change once one
  call can legitimately span multiple windows, each clearing at its
  own price. Proven directly that two windows of the same product get
  independently correct prices, not a value from one bleeding into the
  other.
- `ResponseReserveOrdersProvider.sell_orders()`/`buy_orders()` now
  take `windows: list[tuple[datetime, datetime]]` instead of a single
  window -- fetching only your own target window would silently
  truncate any real competitor's loop or substitutable family that
  extends beyond it, giving a wrong answer without any visible error.
  `backtest_hypothetical_bid()` updated to match, with a new
  `my_window` parameter (defaulting to the first window given) so the
  hypothetical bid can be placed in any of the fetched windows, not
  only the first.

## Two real test-design flaws found and fixed while validating the
## new code, both worth recording rather than silently correcting
1. An early version of the volume-equality test used a 100MW binary
   (PARENT, non-curtailable) sell order against a 20MW buy order --
   infeasible by construction (100 ≠ 20 and 0 ≠ 20 are the only two
   options for a binary variable), not a bug in the equality
   constraint being tested. The previous, looser `<=` version of this
   test would have passed trivially even with zero acceptance, masking
   the flaw; the new equality assertion correctly exposed it. Fixed by
   matching sell and buy volumes exactly (20MW each), so exact
   equality is genuinely achievable.
2. An early overholding test used a binary PARENT sell order and
   expected the zero-priced overholding headroom to go unused. It
   didn't -- and correctly so: with a binary order, "accept all 10MW"
   blends 5MW at the real buy price (£50) with 5MW at the overholding
   price (£0), giving total utility £250 against total cost £200 --
   genuinely welfare-positive on average, even though the marginal
   overholding-matched volume alone contributes nothing. Confirmed by
   hand-calculation before touching the test. Fixed by using a
   curtailable CHILD order instead, which lets the solver actually
   choose to stop exactly at real demand -- the specific behaviour the
   test was meant to verify, which a binary order's all-or-nothing
   choice couldn't isolate.

## Consequences
- 30 tests in `test_eac_auction.py` (up from 19), 354 total ingestion
  tests, no regressions in `core` or `web`.
- What remains deliberately unmodelled, unchanged from ADR 0030: the
  full transfer-aware "Cost Minimisation Pricing" rule (Stage 2 is
  still the marginal-accepted-price approximation) and explicit
  non-negative-surplus enforcement for baskets and looped families --
  both fundamentally Stage 2 (pricing) concerns, and Stage 2 remains a
  stated approximation throughout this module, not a target for
  full replication.
- Not yet done, same as before: running against real, live order
  data, and the marginal-cost-vs-acceptance-probability backtesting
  sweep the original request named as the ultimate goal. The user's
  own stated plan is to reach full EAC fidelity before attempting
  either -- this ADR is that fidelity work, not the sweep itself.
