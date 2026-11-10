# 33. Historical EAC backtest pipeline: ingest NESO orders and results, clear all 12 products, compare

Date: 2026-11-09

## Status
Accepted -- implemented and tested on synthetic data in the real schema.
**Not yet run on real NESO data** (see "What this does not establish").

## Context
ADR 0032 delivered the full clearing engine. The next step, requested by the
user, is to test it on historical auctions from 6 May 2026 (when NESO began
publishing in the new format): ingest the four datasets, clear one day across
all 12 products (Response per 4 h EFA block, Reserve per half hour), and
compare with the published outcome -- in small steps, on a small dataset first.

The four datasets (schemas read from their portal pages, November 2026):
sell orders (22 columns) and buy orders (17) carry both the **inputs** of the
auction and its **outcome** (`acceptanceRatio`, `status`, `executedQuantity`,
`clearingPrice`, `reasonRejected`); results summary (cleared volume and price
per product-window, with `auctionID`); results by unit (no `auctionID`).

## What was found before writing code
1. **No real rows could be read.** The sandbox's shell is blocked from
   api.neso.energy (HTTP 403 from the proxy) and WebFetch refuses it by
   robots.txt. Only schemas were available. Everything is therefore built
   strict and validated on synthetic data; the first real run is the user's.
2. **Features the engine does not model are in the schema.** The June 2024
   design report mentions none of them. Later NESO documents do:
   *Flexible Groups* (Slow Reserve only; `flexibleGroupID`,
   `minimumConsecutiveWindows`) and *linked service windows*
   (`linkedServiceWindowID`). The new `joinedFamily` buy column is
   undocumented. Auctions touching them are not expected to match.
3. The order datasets are **new resources** (`13b511df-...`, `1cf68f59-...`),
   distinct from the "daily" order datasets the older
   `ResponseReserveOrdersProvider` uses; both are kept.

## Decisions
**Inputs and outcomes are kept apart.** Only input columns reach
`SellOrderInput`/`BuyOrderInput`; outcomes are held on the side (a mutation
that leaked the published clearing price into the model is caught by the tests).

**Four comparison lenses**, because matching numbers alone cannot say why
they differ: (1) *reconciliation* of the data against itself; (2) the
*published outcome checked against wattstack's rules* with the independent
`verify_clearing` (also reveals whether NESO behaves like RESTRICTED or
PERMISSIVE overholding, by counting paradoxically accepted buy orders per
window); (3) *welfare* of the published vs simulated outcome (computed from
identically rounded ratios, so rounding alone cannot appear as a gap -- a
spurious -0.04 on ~12,000 was found and fixed); (4) *prices and volumes* per
product-window, with windows sharing a sub-market with an unmodelled feature
scored separately (`tainted_keys`).

**Strict parsing, loud quarantine.** Missing columns, unparseable values and
unknown statuses raise `HistoryDataError` naming dataset/row/column. Data
problems found while building an auction (duplicate order IDs, baskets without
exactly one parent after exclusions) are quarantined and reported, or raise
under `--strict`. Rows before 2026-05-06 are skipped before parsing.

**`EXCLUDED` orders are dropped** (working assumption: never entered the
optimisation); `--excluded keep` tests the opposite; reason codes are shown.

**Exact decomposition** (`clear_auction_by_component`): orders are linked by
every possible coupling (basket, loop, same unit on overlapping windows, same
product-window, buy family); each connected component is its own MILP with the
global price bounds fixed. Verified: identical welfare to the joint solve on
random multi-island markets, in both modes.

**Solver limits.** `clear_auction(time_limit_s=, mip_gap=)`; a time-limited
result is rule-checked and returned with `optimality_proven=False`, which the
report states.

**`_find_violations` is linear in units' baskets** rather than quadratic over
all parents (behaviour-preserving).

## Measured, and a hypothesis that was wrong
Synthetic days: ~1,000 sell orders/250 units: 3 s. One heavily coupled
sub-market of ~2,900 sell orders: ~30 s RESTRICTED, ~5 s PERMISSIVE.
I suspected rule 6's pairwise encoding (`x_a + x_b <= 1`) was the bottleneck
and replaced it with a per-instant clique cover (same integer solutions,
tighter relaxation; welfare identical on 600 random clears). **It was not
faster** (40 s vs 30 s RESTRICTED, same PERMISSIVE) and was reverted; the
slowness is elsewhere (the restricted-overholding binaries are the likely
cause, untested). The behavioural rule-6 tests written for it were kept.

## Tests (182 new; 852 ingestion total)
`test_eac_history.py` 54 (parsing, EFA day incl. clock-change days, case
building, census, download with a fake client, CLI), `test_eac_history_compare.py`
57 (four lenses on hand-calculated scenarios; pipeline round trip on 12
synthetic days covering all 12 products; perturbed data caught by three
lenses), `test_eac_components.py` 68 (what couples orders; welfare equality
vs the joint solve; rule-6 semantics), `test_neso.py` +3. A 24-mutation check
of the new code: all caught. On the first pass (26 mutations) two survived
and one pattern was mistyped: one survivor was an equivalent branch
(removed), the other a real test gap (explicit price bounds reaching
sub-markets; test added). Two mutations aimed at the reverted rule-6
encoding were dropped with it.

## What this does not establish
* Anything about real data: column contents, status semantics, auction
  grouping, price floor/cap, real order volumes and coupling, and whether
  `datastore_search_sql` accepts the date-range SQL (the manual CSV route needs
  no API).
* Accuracy of the engine against NESO. That is what the first real run measures.

## Next steps (in order)
1. User runs Step 1-3 of `docs/eac-history-backtest.md` on one day; send back
   `inspect` output and `report.md`.
2. Fix whatever the reconciliation lens shows (reading errors first).
3. From the clean-vs-flagged gap, decide whether to model Slow Reserve
   Flexible Groups and linked-window equal child ratios.
4. Widen: a week, then the marginal-cost vs acceptance-probability sweep.
