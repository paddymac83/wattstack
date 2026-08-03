# 6. BOALF (acceptances) is volume/timing, not price -- BOD is price

Date: 2026-08-03

## Status
Accepted, correcting a real design mistake, not just a URL bug

## Context
The first version of `marginal_bid_share()` assumed a "price" field
existed directly on Balancing Mechanism acceptance (BOALF) rows, and
took the most extreme accepted price per settlement period as a proxy
for the marginal bid. That assumption was wrong, caught by direct
correction: BOALF records WHICH BM unit was accepted, WHEN, and HOW
MUCH volume (levelFrom/levelTo) -- not price. Price lives in a
separate dataset, Bid-Offer Data (BOD, endpoint `/balancing/bid-offer`),
which is what a BM unit submitted, not what got accepted.

Same session, a second, smaller correction: the acceptances endpoint
itself was called wrong. `/balancing/acceptances/all` needs
`settlementDate` and `settlementPeriod` as query parameters --
`?settlementDate=...&settlementPeriod=...` -- not path segments. An
earlier version guessed path segments by analogy with system-prices,
which was itself only a guess (see ADR 0004), and the guess was
wrong. Confirmed by direct testing, not inferred from documentation.

## Decision
- `bid_offer_acceptances()` now uses the confirmed query-parameter
  URL. `bid_offer_data()` is new, modeled on the same confirmed
  pattern (market-wide `/all` endpoint, same two query params) but
  NOT independently verified -- if it 400s the same way acceptances
  did, that's real information, not a surprise.
- `analysis.price_lookup_by_bmu_period()` builds a `{(bmu, period):
  price}` table from BOD rows -- the most extreme price a unit
  submitted in that period, standing in for "the price of what got
  accepted." This is a second, distinct layer of approximation on
  top of the first (most extreme priced acceptance = "marginal"):
  BOD is the full ladder a unit submitted, not a record of which
  specific rung a given acceptance used. Both approximations are
  named in `analysis.py`'s module docstring, not just here.
- `marginal_bid_share()` now takes a `price_lookup` rather than
  reading price off acceptance rows directly, and periods with no
  known price are excluded from the denominator entirely rather than
  counted as "not a battery" -- an unknown price is a different claim
  than a negative result.
- The notebook fetches and lets you map fields for BOTH datasets now,
  not one -- doubling the real cost (~96 requests/day, not 48).

## Consequences
- Every test in `test_analysis.py` for `marginal_bid_share()` was
  rewritten, not patched -- the function's signature and the meaning
  of its inputs both changed.
- The resulting `share` number is now two approximations deep. Worth
  restating plainly rather than letting the percentage look more
  precise than it is: (1) "marginal" here means single most extreme
  priced action, not BSC's real volume-weighted methodology: (2) that
  price itself is the most extreme price a unit submitted that
  period, not confirmed to be the specific price of what was accepted.
  Both are documented as a starting point to refine, per the whole
  point of building this as an interactive notebook rather than
  shipping a number.
- General lesson worth carrying forward, beyond this specific metric:
  a plausible-sounding single dataset ("accepted bid/offer actions")
  can still be the wrong dataset for a question that sounds like it
  should live there. Checking what a field actually represents, not
  just whether a plausibly-named field exists, is the real defense --
  `verify_schema()` catches missing/renamed fields, but it would not
  have caught this on its own, since `price` was never claimed to
  exist on the wrong dataset by name; the earlier code just invented
  it as a parameter without a field name to check against yet.
