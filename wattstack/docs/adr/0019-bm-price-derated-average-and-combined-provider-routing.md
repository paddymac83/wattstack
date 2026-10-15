# 19. BM price via derated seasonal average, and CombinedPriceProvider routes multiple reserve providers

Date: 2026-08-09

## Status
Accepted -- implemented

## Context
The last piece of the fast-path-to-v1 price stack: BM-Offer and
BM-Bid, following the same real-data seasonal-average approach as
wholesale (ADR 0017) and DC (ADR 0018). BM is genuinely harder than
either: both wholesale (MID) and DC (EAC results) clear at a single
price per period (pay-as-clear); BM is pay-as-bid -- every accepted
bid/offer is paid its own submitted price, so there is no single "the
BM price" to average toward. `MarketSpec`'s own docstring already
states this plainly: BM's reserve price needs to be "an expected-value
proxy... already probability-weighted," not a real forecast, since BM
genuinely cannot be known day-ahead.

Confirmed already, from ADR 0006: BOALF (acceptances) carries
volume/timing, not price; BOD (bid-offer data) is the price-bearing
dataset. `bid_offer_data_for_day()` already existed, built early in
this project, and needed no changes.

Separately, a real architectural gap surfaced once a second reserve
provider (BM) needed to exist alongside the first (DC):
`CombinedPriceProvider`'s original design (ADR 0018) explicitly
deferred this -- "only one reserve_provider for now... will need to
route reserve_prices() by market to whichever provider actually covers
it, not assumed here since that provider doesn't exist yet." That
provider now exists.

Also confirmed directly from the actual current source (not memory)
while answering a question about why `Market.WHOLESALE` isn't in
`MARKET_REGISTRY`: `optimize_day()` treats wholesale as a genuinely
different kind of thing from every reserve market -- `active_reserve
= [m for m in markets if m in MARKET_REGISTRY]` (registry-driven) vs
`wholesale_active = Market.WHOLESALE in markets` (a direct enum
check). `MARKET_REGISTRY` only ever held reserve-style capacity
commitments (a direction, a delivery duration) -- wholesale is the
underlying charge/discharge decision the battery always has available,
not a capacity reservation, and was deliberately never meant to be in
that registry. Not a gap; the intended design, confirmed by reading
`markets.py`'s own module docstring, which already states this.

## Decision
- `ElexonBMPriceProvider`, `reserve_prices()` for BM-Offer/BM-Bid
  only, built on the existing `bid_offer_data_for_day()`. Averages
  *submitted* price levels (`offerPrice` for BM-Offer, `bidPrice` for
  BM-Bid -- genuinely different columns, proven by test that they
  don't get crossed) across all BM units, not filtered to batteries --
  the question here is the general BM opportunity level, not
  battery-specific pricing. Multiplied by `acceptance_derating`
  (default 0.3, a stated, conservative, uncalibrated placeholder --
  correcting it with real acceptance-rate data is exactly the
  acceptance-risk work already on the roadmap, deliberately deferred).
- Shorter default lookback (7 days, vs DC's 90) -- BOD has no bulk
  date-range endpoint the way MID does; `bid_offer_data_for_day()`
  costs 48 requests per day, the same shape B1610's correction
  revealed, so a long lookback here gets expensive fast.
- `CombinedPriceProvider` redesigned: `reserve_providers` (plural,
  list) replaces `reserve_provider` (singular) -- a breaking change,
  made deliberately before any real external caller depended on the
  old shape, rather than carrying backward-compatibility cruft this
  early. `reserve_prices()` tries each provider in turn, catching the
  `ValueError` each one already raises for markets it doesn't cover --
  routing by relying on each provider's own self-declaration, not a
  market-to-provider mapping this class would need to keep in sync by
  hand. Proven directly by test that a DC request reaches the DC
  provider and a BM request reaches the BM provider, not just that
  the API accepts a list
  (`test_reserve_prices_routes_to_the_provider_that_covers_the_market`).

## Consequences
- All three markets in the fast-path-to-v1 plan (wholesale, DC, BM)
  now have real, tested price providers. None are forecasts in the
  predictive sense -- all three are honestly-labelled seasonal
  averages of real historical data, at three different levels of
  fidelity to what they're approximating: wholesale and DC both
  average a genuine single clearing price; BM averages a submitted
  price under a fundamentally different (pay-as-bid) settlement
  mechanism, which is why it alone carries an explicit derating factor
  rather than standing in as a price on its own.
- `acceptance_derating`'s 0.3 default is not calibrated -- stated
  plainly in code and here, not implied to be more rigorous than it
  is. Real historical acceptance-rate data (via BOD+BOALF joined) would
  replace it; not built here.
- BM field names (`offerPrice`, `bidPrice`, `settlementPeriod`) are
  reasoned guesses, same as DC's were before the live correction
  (ADR 0018) -- not yet checked against a real response. Worth the
  same `verify_schema()`-equivalent check before trusting a live run;
  no dedicated verify method was built for BOD specifically here, an
  honest gap rather than an oversight covered up.
