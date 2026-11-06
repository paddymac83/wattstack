# 32. EAC Cost Minimisation Pricing, non-negative-surplus rules, and restricted/permissive overholding

Date: 2026-10-24

## Status
Accepted -- implemented. Supersedes the "deliberately not modelled"
lists in ADR 0030 and 0031 (those ADRs are annotated, not rewritten).

## Context
The user asked for three things "as discussed in ADR 31", to reach full
EAC fidelity before any live backtest: (1) the Cost Minimisation Pricing
rule, (2) explicit non-negative-surplus enforcement for baskets and
looped families, and (3) the permissive version of overholding from
Section 5.3 ("allowing additional buy orders to be paradoxically
accepted") -- plus a straight answer on whether the permissive version
was already modelled.

The design document (https://www.neso.energy/document/282401/download)
was re-read directly before any code was written, because earlier ADRs
cited it from memory. That re-reading corrected several things in
ADR 0030/0031:

- **There is no Section 5.3.1.** Section 5.3 has two numbered items:
  item 1 "allowing at most one paradoxically accepted buy order"
  (restricted) and item 2 "allowing more than one" (permissive, "a more
  relaxed optimisation problem ... solutions of higher welfare for
  exactly equivalent data sets"). ADR 0031 and the module docstring
  cited a nonexistent 5.3.1.
- **Rules 7 and 8 are in Section 6.2**, not Section 7. Rule 7: a child
  or substitutable child may be accepted only with non-negative order
  surplus. Rule 8: a parent may be accepted only if the total surplus of
  all accepted orders in its basket -- or, for looped baskets, in the
  whole looped family -- is non-negative. Section 7 is the pricing
  stage (7.1.1 sell-side conditions, 7.2 cost minimisation, 7.3 bounds).
- **Surplus transfers** (7.1.1) are described only as flowing child ->
  parent and across looped orders. The document does not say they flow
  parent -> child ("NOT FOUND" on a targeted re-read), so that direction
  is not modelled.
- The pricing stage runs **after** acceptance, with acceptance ratios
  fixed (7.2, Appendix A.1).

### The straight answer: was the permissive version already modelled?
**No -- and the earlier description was wrong in the other direction
too.** Through v30 there was no link at all between buy-order
acceptance and the clearing price, and no cap on paradoxical buy
orders. So v30 was not the "restricted, at most one" model its
docstring claimed: it behaved as an *unbounded* permissive model (any
number of buy orders could be accepted regardless of price), while the
restricted version -- the one that actually has a rule to enforce -- did
not exist. `overholding_buy_order()` just added a zero-priced buy order;
nothing limited how many such orders could be used. Both versions are
now real and selectable (`OverholdingMode.RESTRICTED` / `PERMISSIVE`).

## Decision

### Stage 1 is now price-aware, and exact (no relaxation)
Rules 7, 8 and the paradox cap all depend on the clearing price, so
acceptance cannot be chosen first and priced afterwards without risking
an acceptance that no price supports. The obvious encoding -- a price
variable times a fractional acceptance ratio -- is bilinear. Its
standard convex (McCormick) relaxation was considered and rejected: it
is exact only for 0/1 ratios, and with a partially accepted child it
credits the parent with surplus the child does not have. A concrete
case (parent limit 7, child limit 2, a buyer forbidding paradox at a
price of 5) makes the relaxation accept a basket that has no valid price.

Instead the price is eliminated exactly. Sell-side rules only put
**lower** bounds on a window's price; non-paradoxical accepted buy
orders only put **upper** bounds on it. A supporting price therefore
exists if and only if the **highest permitted price** satisfies every
sell-side rule. That highest permitted price can only be the limit of
some buy order that could cap it, or the price ceiling -- a finite set.
Choosing it is a linear selection among binaries, and the surplus
conditions become linear (a group's priced volume is attributed to the
selected level through big-M-bounded volume variables). The whole of
Stage 1 stays an exact MILP.

Consequences of this construction, all by design:
- Buy limit prices decide which acceptances are **priceable**, but never
  the price itself -- consistent with 7.1.2 ("buy orders' limit prices
  have no impact on the computation of the market clearing prices").
- Restricted mode: per (product, window), a flag per buy order marks
  "used paradoxically", capped at one. Permissive mode drops those
  variables entirely (smaller MILP).
- Each buy order carries the document's Section 5.1 Buyer parameter
  `paradoxical_acceptance_allowed` (default True). When False it can
  never be paradoxically accepted, in either mode.

### Stage 2 is Cost Minimisation Pricing, an exact LP (plus one binary per accepted buyer in restricted mode)
With acceptance fixed, one uniform price per (product, window) is chosen
to minimise `sum(accepted sell volume x price)` subject to rule 7 (each
accepted child priced >= its limit), rule 8 (each accepted group's total
surplus >= 0, linear because ratios are now constants), hard upper
bounds from buyers forbidding paradox, the overholding cap on the
actual prices (one binary per accepted buyer, restricted mode only --
see finding 5 for why it cannot live in Stage 1 alone), and the price
bounds. This replaces the old "price of the most expensive accepted order"
approximation. Visible effect: a loss-making parent subsidised by its
child clears at 6.0 in the worked test, where the old rule said 10.0.

### An independent checker guards every result
`verify_clearing()` re-derives rules 1-8, volume balance, price bounds,
the paradox flag and the overholding cap from the raw orders, sharing no
code with the MILP. `clear_auction()` runs it on every result and raises
`PricingInfeasibleError` rather than return an unchecked answer. It is
also public, so a result (or a hand-built one) can be audited.

### Smaller changes
- `OverholdingMode` (default **RESTRICTED**, the document's first-listed
  baseline; the document does not say which version NESO runs live --
  see Next steps).
- `price_bounds` (Section 7.3): explicit bounds are validated (all bids
  inside them, negative minimum allowed); by default derived from the
  data as `[min(0, lowest price), max(0, highest price)]`.
- `ClearingResult` gains `buy_acceptance_ratio_by_order_id`,
  `paradoxically_accepted_buy_order_ids`,
  `paradoxical_buy_volume_mw_by_product_window` (the over-procurement),
  `individual_surplus_by_order_id`, `basket_surplus_by_basket_id`,
  `looped_family_surplus`, `welfare`, `procurement_cost`,
  `overholding_mode`, `price_bounds` -- all defaulted, so existing
  constructors still work.
- `backtest_hypothetical_bid()` passes `overholding_mode` and
  `price_bounds` through.
- Input validation now fails loudly: duplicate order IDs, unknown order
  types, and a basket without exactly one PARENT (a truncated fetch can
  drop a parent row; v30 silently let an orphan child float free) all
  raise `ValueError`.

## Interpretation choices (recorded because the document leaves room)
- "At most one paradoxically accepted buy order" is read literally: one
  per (product, window), of **any** kind -- the overholding order or a
  regular buyer, not one of each.
- Paradoxical acceptance means accepted while the clearing price is
  **strictly** above the order's limit (equality is not paradoxical).
  With a negative clearing price a zero-priced overholding order is
  therefore not paradoxical at all (tested).
- Transfers flow child -> parent and across a looped family only.
- For looped baskets rule 8 applies to the family total, not to each
  basket separately (Section 6.2 rule 8, looped-family clause).

## Findings while validating (recorded, not smoothed over)
1. **A real bug in the delivered v30 package: the `pulp` pin.**
   `ingestion/pyproject.toml` said `pulp>=2.8`, while `core` already
   pinned `<4.0`. A fresh install of v30 resolves PuLP 4.x, which has no
   `LpVariable(cat=...)`, so 21 of the 30 EAC tests fail. The delivered
   zip's tests passed only because the working environment had PuLP 3.x
   cached. Fixed: `pulp>=2.8,<4.0`, matching `core`.
2. **Residual price indeterminacy is real and affects the paradoxical
   set, not just the price split.** In a looped family spanning two
   windows, `p1 + p2 = 12` can be split many ways at identical cost
   (7.2 itself warns of this). Which low buyer counts as paradoxical
   depends on the split. A first version of one test asserted a specific
   set and failed; the determinate facts (cost, at most one per window,
   at least one overall) are what is asserted now.
3. **A vacuous-test guard earned its keep.** The brute-force oracle test
   initially used a uniform random generator on which restricted and
   permissive mode **never disagreed** across 40 seeds -- so matching the
   oracle in both modes proved nothing about the cap. A guard test
   ("modes must disagree on >= 5 seeds") failed, the generator was
   rebuilt to produce lumpy sells needing several small buyers, and the
   suite now has 80 seeds with the guard at >= 5.
4. **Mutation check.** Deliberate mutations of the engine (cap
   removed in Stage 1 and in Stage 2, rule 8 removed in Stage 1 and in
   Stage 2, lone-parent price floor removed, hard-buyer cap removed,
   Stage 2 maximising instead of minimising, paradox flag ignored, a
   wrong big-M in Stage 2's cap) were each caught by the suite. One
   (rule 7 removed from Stage 1) was **not** -- and on investigation it
   is an equivalent mutation: accepting a child at a price ceiling below
   its limit is strictly welfare-reducing (shrink it together with the
   cheap buyer that caps the price), and 600 random baskets under both
   modes gave identical welfare with and without it. This is empirical,
   not a proof. Stage 1 keeps the rule for fidelity to Section 6.2; rule
   7 is independently enforced and tested in Stage 2 and in
   `verify_clearing()`.
5. **A real Stage 2 bug, found by stress test and fixed.** The first
   version enforced the restricted cap only in Stage 1. A general
   random-market stress run (baskets with children, loops across
   windows, several products, buy families, price-capping buyers) hit
   one failure in 800 clears: Stage 2 returned two paradoxical buyers in
   one window, and the independent checker raised
   `PricingInfeasibleError` (so nothing wrong was ever returned). Cause:
   in a looped family the cheapest way to meet the family's required
   total can be to raise ONE window's price above the level Stage 1
   relied on, because that window's seller volume is cheaper to pay
   more. Minimal reproduction (now a regression test): looped S1 (W1,
   limit 10) + S2 (W2, limit 2), an extra W2 seller S3 (limit 1), two
   low W1 buyers; permissive gives p1 = 11, p2 = 1, cost 130 (both low
   buyers paradoxical), restricted must give p1 = 3, p2 = 9, cost 210
   (one paradoxical) -- hand-calculated, and what the fixed code returns.
   Fix: Stage 2 enforces the cap on the actual prices. After the fix,
   4,000 clears (2,000 seeds x both modes) all pass the independent
   checker, slowest 0.05s. Worth noting: the 80-seed single-window
   oracle could never have found this -- it only covers parent-only,
   one-window markets, which is why the broader stress test exists.

## Consequences
- 296 new tests in `tests/test_eac_pricing.py` (650 ingestion total;
  core 54 and web 6 unchanged). They cover: cost-minimisation prices
  (single, multiple, child transfer, partial child, substitutable
  children, looped family, per-window, empty window); rules 7/8 against
  hard-capping buyers (cases where acceptance itself changes); both
  overholding modes with real overholding orders (two small orders vs
  one large one, negative price, per-window and per-product caps, the
  paradox flag, mode agreement when no paradox arises); price bounds;
  input validation; result bookkeeping; every rule in
  `verify_clearing()`; an 80-seed brute-force oracle in both modes
  (single window, parent-only); the coupled-loop regression pair; 150
  general random markets (children, loops, products, buy families, hard
  buyers) that must always pass the independent checker; and the
  backtest pass-through. All 30 earlier EAC tests still pass
  unchanged.
- **Behaviour change vs v30**, deliberately: the default is now
  RESTRICTED, which is stricter than v30's accidental no-cap behaviour,
  so a backtest that v30 accepted via two or more paradoxical buyers will
  now be rejected unless `overholding_mode=PERMISSIVE` is passed.
  Clearing prices also change (lower where transfers apply).
- Cost: Stage 1 now has binaries per price level and per capping buyer
  (plus Stage 2's one per accepted buyer in restricted mode). Slowest
  clear in 4,000 random small markets: 0.05s; **not yet measured on a
  real day of NESO orders**, which will be far larger.

## Not modelled (unchanged or newly named)
- Parent -> child transfers; transfers inside a buy substitutability
  family (glossary mentions them, no mechanics given).
- Rounding of prices to two decimals (7.3 mentions rounded prices).
- Explicit limits on paradoxical **rejection** (7.1.1 lists when it is
  permitted); welfare maximisation decides rejections.
- Welfare ties are broken arbitrarily by the solver.
- Whether NESO's published buy-order dataset carries a
  paradoxical-acceptance column is unconfirmed, so the provider leaves
  every real buy order at the document's default (True).

## Next steps
- Run the backtest on a real day of orders (the sandbox cannot reach
  `api.neso.energy`, so this runs on the user's side) under **both**
  modes and compare each with what actually cleared. That comparison is
  the only available evidence for which Section 5.3 version the live
  auction behaves like; the document does not say.
- Time Stage 1 on real order volumes before building any sweep on it.
- Then the marginal-cost vs acceptance-probability sweep.

## Addendum (2026-11-06): buy-side substitutability tests

A review found buy families were under-tested: one acceptance test that
asserted only `sum <= 1` (which a result rejecting everything also
satisfies), one tamper test of the checker, and ~20% of members in the
random markets. 20 tests were added, all named `test_buyfam_*`, after
re-reading Sections 5.1/5.2 and Appendix B of the design report. What
the report confirms (and the tests pin):

- The constraint is on **acceptance ratios**, sum <= 1 (p.14, p.31) --
  not on MW. Members may differ in volume, product and (overlapping)
  window (B.1-B.3). Buy orders are fully curtailable; "Paradoxical
  acceptance allowed" defaults to True (p.13).
- Not modelled: the operator-configured maximum number of orders per
  family (p.14 restriction 2), and no check that members in different
  windows actually overlap in time (B.3). A family spanning disjoint
  windows is accepted silently; real data presumably never does this.

### Running them in isolation
From `ingestion/` (virtualenv active):

```
pytest tests -k buyfam -v                        # all 20
pytest tests/test_eac_auction.py -k buyfam -v    # 9 acceptance-rule tests
pytest tests/test_eac_pricing.py -k buyfam -v    # 11 pricing-interaction tests
pytest tests -k "buyfam and b1_outcomes" -v      # just the report's Appendix B.1 outcomes
```

| Test (`test_buyfam_...`) | File | Protects |
|---|---|---|
| `only_one_equal_member_clears_and_the_higher_welfare_one_wins` | auction | exactly one member clears, the higher-welfare one (welfare 950) |
| `report_appendix_b1_outcomes` (x4) | auction | all four outcomes listed in Appendix B.1, incl. the 25%/75% split, by forcing each with exact sell supply |
| `constraint_is_on_ratios_so_unequal_volumes_split_by_ratio_not_mw` | auction | 10MW + 20MW members at 0.5/0.5 (welfare 105); a MW cap cannot express this |
| `unequal_volumes_full_acceptance_of_both_members_is_impossible` | auction | ratios 1 + 1 = 2 rejected; the 20MW member wins (140 vs 70) |
| `separate_families_are_independent_of_each_other` | auction | one member from EACH of two families can clear (report B.2) |
| `family_couples_members_across_overlapping_windows` | auction | report B.3: family spans windows |
| `family_picks_the_product_with_higher_welfare_and_prices_only_that_window` (x2) | pricing | substitution + Cost Minimisation Pricing; idle window reports 0.0 |
| `family_member_limit_price_does_not_move_the_clearing_price` (x2) | pricing | Section 7.1.2 for family members |
| `family_blocks_the_two_paradoxical_buyer_trade_even_in_permissive_mode` (x2) | pricing | a family stops the 30MW trade that permissive mode otherwise accepts |
| `control_the_same_trade_without_a_family_is_accepted_in_permissive_mode` | pricing | proves the family, not something else, did the blocking |
| `restricted_mode_uses_the_higher_priced_family_member_as_its_one_paradoxical_buyer` | pricing | family x restricted overholding cap |
| `paradox_flag_on_a_member_changes_which_member_the_family_uses` (x2) | pricing | family x "paradoxical acceptance allowed" flag (940 vs 930) |
| `family_across_overlapping_windows_prices_only_the_winning_window` | pricing | B.3 with prices |

Every expected number is hand-calculated in its docstring. (One was
first mis-added by hand -- seller cost 20MW x 5 = 100, not 200 -- and the
solver's value was right; the test was corrected, not the code.)

### Mutation check
Five deliberate breakages of the family handling were each caught, by
the tests aimed at them: constraint removed (10 tests fail), cap in MW
instead of ratio (5), family applied only within one window (2), only
within one product (9), all families merged into one (1). The paradox-flag
and restricted-slot tests guard different code and were covered by the
earlier mutation runs (above), not by these five.
