# 11. Query parameters go through requests' params=, not hand-built into URL strings

Date: 2026-08-05

## Status
Accepted -- fixed, found by the person building this, not by anything
in this test suite

## Context
Every query-parameter-taking method in `elexon.py` built its URL by
f-string concatenation: `f"{BASE_URL}/path?key={value}"`. Confirmed
broken live: `datetime.isoformat()` on a timezone-aware datetime
produces a literal `+` (e.g. `2026-07-31T09:00:00+00:00`), and `+` in
a URL query string means "space" per `application/x-www-form-
urlencoded` convention, not a plus sign. A hand-built string sends
that `+` completely unescaped. The fix -- passing values through
`requests.get(url, params={...})` instead -- lets `requests` do
correct percent-encoding (`+` becomes `%2B`).

The part worth sitting with, not just the fix: **every test in this
package mocks `requests.get` entirely.** That was a deliberate,
correct choice (no network access from the environment this was
built in, and mocking is how `ingestion/`'s whole test suite runs
without hitting real APIs) -- but it has a real, specific blind spot.
A mocked test can prove the code *constructs the value it intends to
send*. It cannot prove that value survives being turned into an
actual HTTP request correctly, because no actual HTTP request ever
happens. This bug is exactly that gap: every affected test asserted
the hand-built URL string matched what was expected, and passed,
because the test and the code shared the same (wrong) assumption
about how query strings work. Only a live call -- made by the person
using this, not by anything automated here -- could have caught it.

## Decision
- All five query-parameter methods (`bid_offer_acceptances`,
  `bid_offer_data`, `disaggregated_bsad`, `demand_forecast_day_ahead_history`,
  `loss_of_load_forecast`) now build a `params` dict and pass it to
  `requests.get(url, params=..., timeout=...)`. Fixed everywhere the
  pattern appeared, not just the two methods where the bug was
  actually reported -- the other three (settlementDate/settlementPeriod
  values) weren't yet exhibiting the bug (dates and small integers
  don't contain `+`), but shared the identical anti-pattern and the
  identical latent risk.
- Every test that previously asserted against a full URL string now
  asserts against the base URL (`mock_get.call_args.args[0]`) and the
  `params` dict (`mock_get.call_args.kwargs["params"]`) separately --
  more precise than substring-matching a URL, and it's what actually
  changed.
- Two new regression tests added specifically because the existing
  ones couldn't have caught this: `test_timezone_aware_publish_time_plus_offset_goes_through_params_not_the_url_string`
  (`test_elexon.py`) and `test_no_plus_character_leaks_into_any_called_url`
  (`test_notebook_demand_forecast_tightness.py`), both using real
  tz-aware datetimes -- every prior test used naive datetimes, whose
  `isoformat()` never contains a `+` at all, and so could not have
  exercised this even in principle.

## Consequences
- This is still mocked-test coverage, with the same structural limit
  named above. What's different now: the *code* delegates encoding to
  a well-tested library instead of hand-rolling it, which is the
  actual fix -- the tests are better at proving intent, not at
  proving wire correctness, and no amount of additional mocking
  changes that.
- Worth carrying forward as a standing rule for anything added to
  `ingestion/` later: never build a query string by hand. Always
  `params=`. This bug is the concrete reason, not an abstract style
  preference.
- A live integration test (real network, run manually, not part of
  CI) would be the only way to close this gap for real. Not built
  here -- consistent with this whole package's standing limitation
  (no network access in the environment it's developed in) -- but
  worth naming as a real gap rather than implying the mocked suite is
  now sufficient.
