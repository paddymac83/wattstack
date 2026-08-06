"""Minimal client for Elexon's Insights Solution API (BMRS).

No API key required -- confirmed via https://developer.data.elexon.co.uk/
in August 2026. Base URL and the system-prices endpoint were verified
against Elexon's own API documentation at that time, but I could NOT
make a live call from the environment this was written in (no network
access to elexon.co.uk from there). Treat the field names below as a
documented best guess, not a tested fact, until you've run
verify_schema() yourself.

System prices (Settlement System Buy/Sell Price, dataset DISEBSP) are
the closest thing to an "actual outturn price" signal in GB --
distinct from day-ahead auction prices, which Elexon exposes
separately as Market Index Data if you want to add that later.
"""
from __future__ import annotations

from datetime import date, datetime

import requests

from wattstack_ingestion.cache import Cache

BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"

EXPECTED_SYSTEM_PRICE_FIELDS = {
    "settlementDate",
    "settlementPeriod",
    "systemSellPrice",
    "systemBuyPrice",
}


class ElexonClient:
    def __init__(self, cache: Cache | None = None, timeout: int = 30):
        self.cache = cache
        self.timeout = timeout

    def system_prices(self, settlement_date: date) -> list[dict]:
        """One row per settlement period for the given day."""
        cache_key = f"elexon:system-prices:{settlement_date.isoformat()}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        url = f"{BASE_URL}/balancing/settlement/system-prices/{settlement_date.isoformat()}"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        # Insights API endpoints have returned either a bare list or an
        # envelope like {"data": [...]} across different endpoints in the
        # past -- handle both rather than assume.
        records = payload if isinstance(payload, list) else payload.get("data", [])

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def verify_schema(self, sample_date: date) -> set[str]:
        """Fetch one real day and confirm the fields we depend on are
        still there. Call this before trusting anything else in this
        module -- if Elexon has changed the response shape since
        August 2026, this is where you'll find out, loudly, instead
        of silently plotting empty or wrong columns.
        """
        records = self.system_prices(sample_date)
        if not records:
            raise RuntimeError(
                f"Elexon returned zero system-price records for {sample_date}. "
                "Check the date isn't in the future, and that the endpoint URL "
                f"is still {BASE_URL}/balancing/settlement/system-prices/{{date}}."
            )
        fields = set(records[0].keys())
        missing = EXPECTED_SYSTEM_PRICE_FIELDS - fields
        if missing:
            raise RuntimeError(
                f"Elexon system-prices response is missing expected fields {missing}. "
                f"The API schema has likely changed since August 2026. Got fields: {sorted(fields)}"
            )
        return fields

    def bid_offer_acceptances(self, settlement_date: date, settlement_period: int) -> list[dict]:
        """Accepted Balancing Mechanism bid/offer actions for a single
        settlement period -- WHICH BM unit was accepted, WHEN, and
        HOW MUCH volume (levelFrom/levelTo). This does NOT include
        price -- BOALF (acceptances) and BOD (bid-offer submissions,
        see bid_offer_data()) are separate datasets. Getting this
        wrong the first time is what produced the old, incorrect
        version of marginal_bid_share() -- see ADR 0006.

        URL confirmed correct by direct testing: settlementDate and
        settlementPeriod are query parameters, not path segments --
        an earlier version of this method guessed path segments by
        analogy with system-prices, and that guess was wrong.
        """
        cache_key = f"elexon:acceptances:{settlement_date.isoformat()}:{settlement_period}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        url = (
            f"{BASE_URL}/balancing/acceptances/all"
            f"?settlementDate={settlement_date.isoformat()}&settlementPeriod={settlement_period}"
        )
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        records = payload if isinstance(payload, list) else payload.get("data", [])

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def bid_offer_data(self, settlement_date: date, settlement_period: int) -> list[dict]:
        """Submitted Bid-Offer Data (BOD) for a single settlement
        period -- the price/volume ladder each BM unit submitted, NOT
        which parts of it were actually accepted (that's
        bid_offer_acceptances() / BOALF). Getting a price for what was
        actually accepted means joining the two on BM unit and
        settlement period -- see analysis.price_lookup_by_bmu_period()
        and ADR 0006 for the real limitation in doing that precisely.

        UNVERIFIED against live traffic. Modeled on the confirmed
        acceptances fix above (query params, market-wide /all
        endpoint) rather than independently tested -- if this errors
        the same way acceptances did, query params are still the
        first thing to check, but don't assume this one's right just
        because that one's pattern matched.
        """
        cache_key = f"elexon:bid-offer:{settlement_date.isoformat()}:{settlement_period}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        url = (
            f"{BASE_URL}/balancing/bid-offer/all"
            f"?settlementDate={settlement_date.isoformat()}&settlementPeriod={settlement_period}"
        )
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        records = payload if isinstance(payload, list) else payload.get("data", [])

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def bid_offer_acceptances_for_day(self, settlement_date: date) -> list[dict]:
        """All 48 settlement periods' acceptances for one day.

        This is 48 separate requests, not one -- a full day of
        acceptances costs roughly 48x what a full day of system prices
        costs. Each period is cached individually, so re-running after
        a partial failure only re-fetches what's missing, and repeat
        calls for the same day are free. Still, don't reach for this
        across many days without thinking about it -- a week is
        already ~336 requests to a free, no-API-key, public service.
        """
        records: list[dict] = []
        for period in range(1, 49):
            records.extend(self.bid_offer_acceptances(settlement_date, period))
        return records

    def bid_offer_data_for_day(self, settlement_date: date) -> list[dict]:
        """All 48 settlement periods' submitted bid-offer prices for
        one day. Same cost profile as bid_offer_acceptances_for_day():
        48 requests, each period cached independently.
        """
        records: list[dict] = []
        for period in range(1, 49):
            records.extend(self.bid_offer_data(settlement_date, period))
        return records

    def disaggregated_bsad(self, settlement_date: date, settlement_period: int) -> list[dict]:
        """Disaggregated Balancing Services Adjustment Data (DISBSAD)
        -- 'BSAA' in SPAR's own terminology: balancing volume from
        outside the ordinary Bid/Offer stack (system-to-system
        services, STOR taken outside the BM, forward contracted
        energy products).

        UNVERIFIED against live traffic, same status as
        bid_offer_data(): the query-param pattern is modeled on the
        confirmed acceptances fix (ADR 0006), not independently
        tested. Endpoint path found in Elexon's own API docs as
        /balancing/nonbm/disbsad/details -- if this 400s, query
        params vs path segments is the first thing to check, the same
        lesson as last time.
        """
        cache_key = f"elexon:disbsad:{settlement_date.isoformat()}:{settlement_period}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        url = (
            f"{BASE_URL}/balancing/nonbm/disbsad/details"
            f"?settlementDate={settlement_date.isoformat()}&settlementPeriod={settlement_period}"
        )
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        records = payload if isinstance(payload, list) else payload.get("data", [])

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def disaggregated_bsad_for_day(self, settlement_date: date) -> list[dict]:
        """All 48 periods, same cost profile as the other _for_day
        methods -- 48 requests. Combined with
        bid_offer_acceptances_for_day() for a full day's picture (BM
        actions + non-BM BSAA actions), that's ~96 requests per day --
        a full month is ~2,900. Worth thinking about before reaching
        for a whole month, not after.
        """
        records: list[dict] = []
        for period in range(1, 49):
            records.extend(self.disaggregated_bsad(settlement_date, period))
        return records

    def bm_units_reference(self) -> list[dict]:
        """Standing reference data for every BM Unit -- the only way
        to know which BM unit IDs are batteries. UNVERIFIED against
        live traffic; field names (particularly whatever encodes fuel
        type / technology) were not confirmed. This barely changes
        day to day so it's worth caching aggressively once you've
        confirmed it works.
        """
        cache_key = "elexon:bmunits:all"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        url = f"{BASE_URL}/reference/bmunits/all"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        records = payload if isinstance(payload, list) else payload.get("data", [])

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def demand_forecast_day_ahead(self) -> list[dict]:
        """Latest day-ahead national demand forecast (NDF/TSDF).

        Endpoint path confirmed directly from Elexon's own API client
        source (demand_forecast_api.py: GET /forecast/demand/day-ahead,
        no required parameters) -- more solidly confirmed than most of
        this module, since it came from reading the actual client
        code, not documentation. Field NAMES inside each row were NOT
        confirmed the same way; the endpoint's own description says
        this covers National Demand Forecast (NDF, national-level,
        excludes station transformer/pumped storage/interconnector
        load) and Transmission System Demand Forecast (TSDF, national
        and zonal, includes them) -- plausibly two figures per row,
        but the literal JSON keys are a guess until checked.
        """
        cache_key = "elexon:demand-forecast:latest"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        url = f"{BASE_URL}/forecast/demand/day-ahead"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        records = payload if isinstance(payload, list) else payload.get("data", [])

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def demand_forecast_day_ahead_history(self, publish_time: datetime) -> list[dict]:
        """Day-ahead national demand forecast, as it stood at
        publish_time -- the vintage-retrieval mechanism
        ForecastProvider (see forecasts.py) is built on.

        Endpoint path and the `publishTime` query parameter name
        confirmed directly from Elexon's own API client source
        (demand_forecast_api.py: GET /forecast/demand/day-ahead/history,
        required `publish_time` -> query param `publishTime`).

        Pass a timezone-aware datetime (UTC) if at all possible. GB
        market timing (gate closure, forecast release cadence) is
        specifically about which moment "09:00" means -- a naive
        datetime is sent as-is via isoformat(), which risks silently
        meaning the wrong instant rather than erroring.
        """
        cache_key = f"elexon:demand-forecast:history:{publish_time.isoformat()}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        url = f"{BASE_URL}/forecast/demand/day-ahead/history"
        payload = {"publishTime": publish_time.isoformat()}

        # Make the request (requests will safely encode '+' to '%2B')
        response = requests.get(url, params=payload, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        records = payload if isinstance(payload, list) else payload.get("data", [])

        if self.cache is not None:
            self.cache.set(cache_key, records)
        return records

    def verify_demand_forecast_schema(self) -> set[str]:
        """Fetch the latest demand forecast and confirm it's
        reachable and returns real rows. Can't assert specific field
        names since none were confirmed live (see
        demand_forecast_day_ahead()'s docstring) -- this confirms the
        endpoint still works and shows you what's actually there.
        """
        records = self.demand_forecast_day_ahead()
        if not records:
            raise RuntimeError(
                "Elexon returned zero demand forecast records -- check the endpoint is still "
                f"{BASE_URL}/forecast/demand/day-ahead."
            )
        return set(records[0].keys())
