# Backtesting wattstack's EAC clearing against NESO's published auctions

`wattstack-eac-history` loads NESO's historical sell orders, buy orders and
results, clears the same auction with wattstack's engine
(`eac_auction.clear_auction_by_component`), and writes a report comparing the
two. It covers **all 12 products** (Response: DRL DRH DML DMH DCL DCH, per
4-hour EFA block; Reserve: NSR PSR NQR PQR NBR PBR, per half hour) cleared
**jointly**, as NESO's single auction does.

Status: the pipeline is built and tested on **synthetic** data in the real
schema. It has **not yet been run on real NESO data** -- the development
sandbox cannot reach `api.neso.energy`. Step 1 below is therefore the first
real test, and `inspect` exists so that a schema surprise shows up in seconds,
before any solver time is spent.

## Setup

```
cd ingestion
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Step 0 -- see it work on synthetic data (no network, ~1 s)

```
wattstack-eac-history synth --out /tmp/synth --day 2026-05-07 --seed 1
wattstack-eac-history inspect --data /tmp/synth --day 2026-05-07
wattstack-eac-history run     --data /tmp/synth --day 2026-05-07 --out /tmp/synth_out --mode both
cat /tmp/synth_out/report.md
```

The "published" outcome in synthetic data is wattstack's own clearing, so it
must match 100% (RESTRICTED mode). If it does not, the pipeline is broken.

## Step 1 -- get the real data

**Option A -- download through the NESO API** (needs access to api.neso.energy):

```
wattstack-eac-history download --start 2026-05-07 --out data/2026-05-07
```

This pulls the day's results summary, then *every* sell and buy order of the
auctions it names (whole auctions, because looped baskets and families can
reach outside the day), then results-by-unit. Use `--end` for a range.

**Option B -- download the CSVs by hand** from the four portal pages and save
them in one folder under these names:

| Portal dataset | File name |
|---|---|
| Response-Reserve sell orders | `sell_orders.csv` |
| Response-Reserve buy orders | `buy_orders.csv` |
| Response-Reserve results summary | `results_summary.csv` |
| Response-Reserve results by unit | `results_by_unit.csv` (optional) |

Rows delivering before 2026-05-06 (the old layout) are skipped automatically
(`--since`).

## Step 2 -- inspect (fast, no solving)

```
wattstack-eac-history inspect --data data/2026-05-07 --day 2026-05-07
```

Read this output before running anything heavy:

* **Parse errors** name the dataset, row and column. The parsers are strict on
  purpose: they raise rather than guess at a column's meaning.
* **sub-markets / largest N orders** is the size of the biggest optimisation.
  Everything that cannot interact is solved separately.
* **NOT MODELLED but present** lists features in the data that
  `clear_auction` does not implement (see below).
* **reconciliation** checks the published data against itself (executed =
  quantity x ratio; sell volume = buy volume = summary volume; results-by-unit
  = order totals). A failure means a column is not what the code assumes.
* **real outcome vs wattstack rules** runs the independent rule checker on
  NESO's own published result.
* **reason codes** are the strings NESO gives for REJECTED/EXCLUDED orders.

## Step 3 -- run

```
wattstack-eac-history run --data data/2026-05-07 --day 2026-05-07 --out out/2026-05-07 --mode both
```

Useful options: `--auction-id N` (one auction), `--time-limit 300` (CBC seconds
per sub-market; the report says if it was hit), `--mip-gap 0.001`,
`--excluded keep` (try EXCLUDED orders as ordinary orders),
`--price-bounds LO HI` (NESO's technical min/max price, Section 7.3),
`--basis calendar` (UK midnight-to-midnight instead of the 23:00 EFA day),
`--strict` (fail on data issues instead of quarantining them),
`--products DCL,DCH` (smoke test only: distorts the market, and the report
says so).

Outputs in `--out`: `report.md` (read this first), `auctions.csv`,
`windows.csv` (one row per product-window), `sell_orders.csv`, `buy_orders.csv`
(one row per order, actual vs simulated ratio), `reconciliation_findings.csv`,
`run.json`.

## How to read the report

The comparison has four lenses, because "do the numbers match?" cannot say why
they do not.

1. **Reconciliation (section 5)** -- does the published data agree with itself?
2. **Real outcome vs wattstack's rules (section 2)** -- the independent checker
   run on NESO's published acceptance ratios and prices. A violation names the
   rule wattstack models differently from NESO (or an input it reads wrongly).
   It also counts paradoxically accepted buy orders per product-window: more
   than one in some windows means NESO behaves like PERMISSIVE overholding
   (design report Section 5.3).
3. **Welfare (section 1)** -- real outcome's welfare vs the simulated optimum.
   Real outcome legal under wattstack's rules but *lower* welfare => the real
   auction faced a constraint wattstack lacks. Wattstack lower (and proven
   optimal) => wattstack is over-constrained.
4. **Prices and volumes (sections 1, 3, 4)** -- per product-window, with
   windows touched by unmodelled features scored separately.

Tolerances: prices 0.011 GBP/MW/h (NESO rounds up to the penny; the simulated
price is rounded up the same way before comparing), volume 0.05 MW, ratio
0.01. Order-level differences with equal welfare are ties, not errors.

## What the engine does not model (reported, never hidden)

| In the data | Meaning | Status |
|---|---|---|
| `flexibleGroupID`, `minimumConsecutiveWindows` (sell) | Slow Reserve **Flexible Groups**: >= 4 baskets over consecutive windows; accepted if the group's total surplus >= 0 and accepted consecutive windows are not shorter than the Service Window Block | Not modelled. Counted in the census; affected windows scored separately. |
| `linkedServiceWindowID` (summary) | NESO links consecutive windows: the same units at the same volume; parents all-or-nothing and the n-th child of each basket at the same ratio | Parents-together is covered by looped families; the equal child ratios are not modelled. |
| `joinedFamily` (buy) | Not documented on the portal | Captured and counted, not used. |
| Price rounding, parent-to-child surplus transfer, paradoxical-rejection restrictions, operator max orders per family | See ADR 0030-0032 | Not modelled. |

Sources: the Slow Reserve Procurement Rules (para 9.5, 10.2.15) and N-SIDE's
"Linked service windows" page; see ADR 0033.

## Working assumptions to confirm on the first real run

* `status = EXCLUDED` means the order never entered the optimisation (dropped
  by default; `--excluded keep` tests the opposite). The reason codes in the
  census should settle it.
* Timestamps are UTC (as noted in `neso.py`); the delivery day is the EFA day
  (23:00 to 23:00 UK time).
* `auctionID` identifies one clearing run; every auction touching the day is
  cleared whole.
* Window lengths: 4 h for Response, 30 min for Reserve. Others are reported.
* The price floor/cap are derived from the data unless `--price-bounds` is
  given. The report shows the observed range of real clearing prices; NESO's
  floor often shows up as the price of windows with spare capacity.

## Speed

Measured on SYNTHETIC days (laptop-class CPU, CBC): ~1,000 sell orders over
250 units -- 3 s. A single heavily coupled sub-market of ~2,900 sell orders
(120 units x 8-16 baskets each) -- ~30 s RESTRICTED, ~5 s PERMISSIVE.
Real order volumes and coupling are unknown; use `inspect` to see the largest
sub-market first, and `--time-limit` to bound the worst case.
