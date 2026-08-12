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

DC requirement forecasts (below) are a genuinely different access
pattern from everything else in this module: confirmed directly from
https://www.neso.energy/data-portal/dynamic-containment-4-day-forecast
(August 2026) to be plain CSV downloads, NOT datastore-active
resources -- there's no `datastore/dump/` link, only `download/*.csv`,
the same situation RESULTS_SUMMARY's docstring already flags for a
different dataset. `fetch_csv()` fetches and parses these directly
rather than assuming `datastore_search` works, since there's no
evidence it does for this specific dataset.

That same NESO page also confirms, directly in its own text, the
methodology DC requirements are built from: "forecasted demand,
inertia, and response volumes as well as a view of the largest losses
on the system." System Inertia (also below) is a separate, confirmed
datastore-active dataset -- but it's OUTTURN inertia (what inertia
actually was), not a forward inertia forecast; no separate structured
"inertia forecast" dataset was found with a confirmed public endpoint
(NESO's "GB Inertia Forecasting" page describes an internal
methodology/research project, not an obviously downloadable dataset).
Using outturn inertia against outturn DC requirements (both from
history resources) is a reasonable, honest way to test the
hypothesis that inertia drives requirement variation -- it just isn't
the same thing as a forward-looking match to the DC forecast's own
vintage.

Separately: no confirmed dataset was found for "largest secured loss"
or "other response product levels" as their own structured, fetchable
resources. The DC requirement CSV's own schema (once fetched) is the
first place to check whether either is exposed as a column there --
genuinely unknown until checked, not assumed either way.
"""
from __future__ import annotations

import csv
import io

import requests

from wattstack_ingestion.cache import Cache

BASE_URL = "https://api.neso.energy/api/3/action"

# Confirmed directly from
# neso.energy/data-portal/dynamic-containment-4-day-forecast, August
# 2026 -- full download URLs, not just resource IDs, since the
# filename pattern embeds the resource ID inconsistently between the
# two resources and can't be reliably reconstructed from the ID alone.
DC_REQUIREMENTS_CURRENT_URL = (
    "https://api.neso.energy/dataset/c8cd1e99-bc7e-454c-8ac1-ebd9264f4d0f/resource/"
    "1b85a3f3-80f0-49cf-9b0e-49648fa0cae6/download/"
    "dcrequirements_1b85a3f3-80f0-49cf-9b0e-49648fa0cae6_patch.csv"
)
DC_REQUIREMENTS_HISTORY_URL = (
    "https://api.neso.energy/dataset/c8cd1e99-bc7e-454c-8ac1-ebd9264f4d0f/resource/"
    "d5c3b48a-a0a9-4d57-a02a-ac0af09a6298/download/"
    "dcrequirementshistorical1_d5c3b48a-a0a9-4d57-a02a-ac0af09a6298_patch.csv"
)

# System Inertia (outturn, not forecast) -- confirmed datastore-active,
# one resource per financial year. Each ID individually confirmed via
# that year's own data-portal page (August 2026 search), not inferred
# from a naming pattern. No 2025-2026 resource was found in that
# search -- 2024-2025 is the most recent confirmed one; a newer
# resource very likely exists given this consistent yearly pattern,
# just not confirmed yet. Pass a resource_id explicitly to
# system_inertia() once you have it.
SYSTEM_INERTIA_RESOURCES = {
    "2019-2020": "2f2dbaa1-3047-4e48-85f2-ec24e669678f",
    "2021-2022": "55161fb4-1396-46e2-9250-2e2b9df904bf",
    "2022-2023": "b42a4fe0-32e4-47ed-9697-5fe101b75970",
    "2023-2024": "5bd6ec4d-a2df-4c94-9b27-fdf8cf04d7dd",
    "2024-2025": "7a12d0bd-448d-42a9-b333-4a32761dbad4",
}

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

    def datastore_search(
        self, resource_id: str, limit: int = 1000, offset: int = 0, sort: str | None = None
    ) -> list[dict]:
        """`sort` uses CKAN's own syntax directly, e.g. "deliveryStart desc"
        -- a real, standard CKAN datastore_search parameter, not
        previously used anywhere in this module. Matters more than it
        might look: without an explicit sort, a plain limit=N fetch
        against a large, continuously-growing dataset has no
        guaranteed relationship to recency -- could just as easily
        return the oldest N records as the newest, depending on the
        database's internal ordering. Pass sort explicitly whenever
        "the most recent N records" is actually what's wanted.
        """
        cache_key = f"neso:datastore_search:{resource_id}:{limit}:{offset}:{sort}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        params = {"resource_id": resource_id, "limit": limit, "offset": offset}
        if sort is not None:
            params["sort"] = sort
        response = requests.get(f"{BASE_URL}/datastore_search", params=params, timeout=self.timeout)
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

    def fetch_csv(self, url: str) -> list[dict]:
        """Fetch a plain CSV download and parse it into dicts --
        the access pattern DC requirements need (confirmed NOT
        datastore-active, see this module's docstring), distinct from
        every other method here which goes through datastore_search's
        JSON API.

        Cached by URL, same as datastore_search rows are cached by
        resource_id -- the fetch is the expensive part either way.
        """
        cache_key = f"neso:csv:{url}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        records = list(reader)

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def dc_requirements_forecast_current(self) -> list[dict]:
        """Dynamic Containment Low and High requirement forecast, next
        4 days -- confirmed live via CSV download, not datastore_search
        (see module docstring). Field names not independently
        confirmed; verify_dc_requirements_schema() shows the real ones.
        """
        return self.fetch_csv(DC_REQUIREMENTS_CURRENT_URL)

    def dc_requirements_forecast_history(self) -> list[dict]:
        """Historical DC Low/High requirement forecasts -- the same
        dataset's history file. NESO's own page doesn't document a
        publish-time/vintage query mechanism for this CSV the way the
        Elexon /history endpoints work (ADR 0009) -- this appears to
        be the full historical series in one file, not something you
        query by an as-of time. Confirm this by looking at what's
        actually in it; don't assume a vintage mechanism exists here.
        """
        return self.fetch_csv(DC_REQUIREMENTS_HISTORY_URL)

    def verify_dc_requirements_schema(self) -> set[str]:
        """Fetch the current DC requirements CSV and confirm it's
        reachable and returns real rows -- also where you'd discover
        whether largest-loss or other-response-level data happens to
        be exposed as columns here (genuinely unknown until checked).
        """
        records = self.dc_requirements_forecast_current()
        if not records:
            raise RuntimeError(
                "NESO returned zero DC requirements records -- check the download URL is still live: "
                f"{DC_REQUIREMENTS_CURRENT_URL}"
            )
        return set(records[0].keys())

    def system_inertia(self, resource_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        """Outturn (not forecast) system inertia for GB, per
        settlement period -- one resource per financial year, see
        SYSTEM_INERTIA_RESOURCES. Pass the specific year's resource_id
        explicitly; there's no "latest" alias confirmed to exist.
        """
        return self.datastore_search(resource_id, limit=limit, offset=offset)

    def verify_system_inertia_schema(self, resource_id: str) -> set[str]:
        """Fetch a small sample and confirm the resource_id is live
        and returns rows -- same purpose as verify_schema(), for a
        resource not in KNOWN_RESOURCES.
        """
        records = self.datastore_search(resource_id, limit=5)
        if not records:
            raise RuntimeError(
                f"NESO system inertia resource '{resource_id}' returned zero records -- "
                "check the resource_id is correct and the dataset hasn't been retired."
            )
        return set(records[0].keys())
