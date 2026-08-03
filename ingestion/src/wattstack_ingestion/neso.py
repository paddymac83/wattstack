"""Minimal client for NESO's Data Portal (CKAN-based).

No API key required. Base action API: https://api.neso.energy/api/3/action

KNOWN_RESOURCES below now includes CONFIRMED, current, live resource
IDs for the EAC auction results dataset (response services DC/DM/DR
plus reserve services BR/QR/SR, all now procured through the same
platform) -- verified directly from
https://www.neso.energy/data-portal/eac-auction-results in August
2026, updated daily per that page. This superseded an earlier version
of this module that could only point at a frozen 2021-2023 dataset;
that one is kept below too since it's still the only source for
anything before November 2023.

Two honest gaps that remain, both by design rather than oversight:
  - `RESULTS_SUMMARY` is confirmed reachable via `datastore_search`
    (its listing page offers a `datastore/dump/` download link, which
    only exists for datastore-active resources in CKAN -- that's what
    justifies using datastore_search here rather than CSV-parsing a
    static file). The four "Daily ..." resources on the same page
    (only offered as plain `.../download/*.csv` links, no
    `datastore/dump/`) are NOT confirmed to work the same way, and
    aren't wired up here -- they may need CSV parsing over the
    download URL instead of datastore_search.
  - The exact field names inside RESULTS_SUMMARY were not confirmed
    live. NESO's own page does confirm the dataset uses
    `deliveryStart` / `deliveryEnd` datetime fields (UTC, not local
    time) -- that's a real, sourced detail, not a guess -- but the
    price/volume column names were not. verify_schema() will show you
    the real ones on first run.
"""
from __future__ import annotations

import requests

from wattstack_ingestion.cache import Cache

BASE_URL = "https://api.neso.energy/api/3/action"

KNOWN_RESOURCES = {
    # Confirmed via neso.energy/data-portal/eac-auction-results, August
    # 2026. Covers DC/DM/DR response + BR/QR/SR reserve services,
    # November 2023 onwards, updated daily. This is the one to start
    # with for anything current.
    "response_reserve_results_summary": "596f29ac-0387-4ba4-a6d3-95c243140707",
    # Same dataset, broken out per BMU rather than aggregated -- the
    # right one if you want acceptance/clearing behaviour at the
    # individual-unit level rather than a market-wide summary.
    "response_reserve_results_by_unit": "a63ab354-7e68-44c2-ad96-c6f920c30e85",
    # Submitted orders (not just cleared results) -- useful later for
    # understanding bid depth/competition, not wired into cli.py yet.
    "response_reserve_buy_orders": "1cf68f59-8eb8-4f1d-bccf-11b5a47b24e5",
    "response_reserve_sell_orders": "13b511df-d6ec-4143-afb1-0ecc6fd19810",
    # Historical only, does not update -- the sole source for anything
    # before November 2023 (the old Dynamic Containment-only tenders).
    "dc_dr_dm_summary_2021_2023": "888e5029-f786-41d2-bc15-cbfd1d285e96",
}


class NesoClient:
    def __init__(self, cache: Cache | None = None, timeout: int = 30):
        self.cache = cache
        self.timeout = timeout

    def datastore_search(self, resource_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        cache_key = f"neso:datastore_search:{resource_id}:{limit}:{offset}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        response = requests.get(
            f"{BASE_URL}/datastore_search",
            params={"resource_id": resource_id, "limit": limit, "offset": offset},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(f"NESO datastore_search reported failure: {payload}")
        records = payload["result"]["records"]

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def response_reserve_results_summary(self, limit: int = 1000, offset: int = 0) -> list[dict]:
        """Current (Nov 2023 onwards) EAC auction results, market-wide."""
        return self.datastore_search(KNOWN_RESOURCES["response_reserve_results_summary"], limit=limit, offset=offset)

    def response_reserve_results_by_unit(self, limit: int = 1000, offset: int = 0) -> list[dict]:
        """Current EAC auction results, broken out per BMU."""
        return self.datastore_search(KNOWN_RESOURCES["response_reserve_results_by_unit"], limit=limit, offset=offset)

    def dc_dr_dm_summary_historical(self, limit: int = 1000) -> list[dict]:
        """Pre-November-2023 only. See KNOWN_RESOURCES docstring."""
        return self.datastore_search(KNOWN_RESOURCES["dc_dr_dm_summary_2021_2023"], limit=limit)

    def verify_schema(self, resource_key: str = "response_reserve_results_summary") -> set[str]:
        """Fetch a small sample and confirm we're getting real rows
        back. Confirms the resource_id is live and returns rows, not
        that every field matches what a plotting function expects --
        print and check the result by eye the first time you use a
        resource, same as glasshouse's schema-drift discipline.
        """
        records = self.datastore_search(KNOWN_RESOURCES[resource_key], limit=5)
        if not records:
            raise RuntimeError(
                f"NESO resource '{resource_key}' ({KNOWN_RESOURCES[resource_key]}) "
                "returned zero records -- the resource_id may be wrong or the "
                "dataset may have been retired."
            )
        return set(records[0].keys())
