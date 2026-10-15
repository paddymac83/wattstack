# 4. ingestion/ is built against researched, not fully load-tested, API shapes

Date: 2026-08-03 (updated same day once real NESO resource IDs were confirmed)

## Status
Accepted, with a clear verification step required before trusting it

## Context
`ingestion/` needed real Elexon and NESO clients. The environment
this was built in has no network access to elexon.co.uk or
neso.energy -- only package registries. So the client code is based
on reading Elexon's and NESO's own current API documentation, not on
a live successful call from within that environment.

Elexon gap, partially closed: the response envelope (bare list vs.
`{"data": [...]}`) for system-prices could not be confirmed and is
still handled defensively either way. Separately, the bid-offer
acceptances endpoint was confirmed wrong by direct experience: the
person building this called it with a settlement date alone and got
a real error. `/balancing/acceptances/all` requires a settlement
period as well -- now a second path segment, following the pattern
every other confirmed Elexon date+period endpoint uses. Fetching a
full day of acceptances is therefore 48 requests, not 1;
`bid_offer_acceptances_for_day()` makes that cost explicit rather
than hiding a loop inside what looks like a single call, and the
notebook's UI says so directly rather than only in a docstring.
Field names inside an acceptance row are still unconfirmed.

NESO gap, since closed: the first version of this ADR flagged that
NESO's own site marked live Dynamic Services results as "(WIP)" and
that no current resource_id could be confirmed. The person building
this then supplied the actual EAC auction results page directly
(https://www.neso.energy/data-portal/eac-auction-results), which
gave real, current resource IDs (covering DC/DM/DR + BR/QR/SR,
November 2023 onwards, updated daily) -- now in `KNOWN_RESOURCES`.
What's still genuinely unconfirmed: the exact price/volume field
names inside that resource. NESO's page did confirm the datetime
fields (`deliveryStart`/`deliveryEnd`, UTC) directly, which is why
`NESO_DATE_FIELD` in `cli.py` is set rather than left blank --
`NESO_PRICE_FIELD` is not, because nothing sourced confirmed it.

## Decision
`verify_schema()` on both clients is not optional decoration --
`wattstack-explore --verify-only` must be run and pass before the
rest of the module is trusted. Both functions fail loudly with a
specific message naming what's missing, rather than silently
returning wrong or empty columns. Same discipline as glasshouse's
ingestion schema-drift script, applied here from the start rather
than added after a silent failure.

## Consequences
- First thing to do with this package: run `wattstack-explore
  --verify-only` and actually read its output before running anything
  that produces charts.
- `NESO_PRICE_FIELD` in `cli.py` stays `None`, and NESO plotting stays
  inert, until you've looked at `verify_schema()`'s real field list
  and filled it in by hand.
- Worth remembering as a pattern, not just a one-off: giving a source
  a concrete URL to check (as happened here) is a fast way to convert
  an honestly-flagged unknown into a confirmed fact, faster than
  guessing and faster than leaving it unresolved indefinitely.
