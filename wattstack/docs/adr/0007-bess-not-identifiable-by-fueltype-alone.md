# 7. BESS units aren't reliably identifiable by fuelType alone

Date: 2026-08-04

## Status
Accepted -- a combined signal, not a fix that closes the gap outright

## Context
`filter_battery_bmu_ids()` identified batteries by matching a label
(e.g. "battery") against `fuelType` on `/reference/bmunits/all`.
Confirmed against live data: `fuelType` does not reliably tag BESS
units at all. This isn't a wattstack bug -- independent confirmation
found while researching this (a 2019 analysis of the same Elexon
data) describes the fuel-type field as having known accuracy and
completeness gaps generally, not just for storage.

A specific fix was proposed: identify batteries by a BM Unit ID
naming pattern instead (IDs ending in a letter + dash + number, e.g.
`T_KILSB-2`). Checked before accepting it, rather than trusted
because it looked reasonable: several real, well-known GB power
stations use exactly that shape for unrelated reasons (a second
generating unit at the same site, not storage) -- Aberthaw B,
Dungeness B, Hinkley Point B, Ironbridge B, Rugeley B, and Tilbury B
are all real, named BM units that a naive "ends in B-<number>"
pattern would also match.

## Decision
`filter_bmus_by_id_pattern()` is a new, generic regex matcher against
BM unit IDs -- deliberately shipped with no hardcoded default
pattern, so nobody using it inherits an unstated assumption. In
`notebooks/explore.py`, battery identification is now the union of
the existing fuel-type-label match and this ID-pattern match, with
the actual matched unit list displayed in a table before the
marginal-bid-share result -- checking the list is presented as a
required step, not optional polish, since the pattern's own
false-positive risk is real and demonstrated (see
`test_id_pattern_also_demonstrates_its_own_false_positive_risk`,
which asserts Dungeness B genuinely gets caught by the naive pattern
in the same test that confirms it correctly catches a real battery
fuelType alone would miss).

## Consequences
- This does not solve battery identification -- it makes the two
  best available signals easy to combine and, critically, easy to
  check by eye, which a single opaque boolean would not be.
- Before trusting a chart's battery-derived numbers, look at the
  matched-units table. A pattern that pulls in an obviously-wrong
  unit (a known nuclear or CCGT station) means the pattern needs
  tightening for that specific naming convention, not that the whole
  approach is broken.
- `spar_accepted_offer_volume_by_fuel_type.py` now uses the same
  combined approach: matched battery units are forced into their own
  `"BESS"` category, overriding whatever `fuelType` says for them
  (confirmed by test to matter -- a battery with `fuelType: "OTHER"`
  was previously invisible as its own segment, silently absorbed into
  a generic bucket). Both notebooks now share this treatment; neither
  is left inconsistent with the other.
