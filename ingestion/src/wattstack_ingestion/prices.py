"""Real price providers, structurally compatible with wattstack_core's
PriceProvider protocol -- same placement reasoning as forecasts.py
(ADR 0009): core doesn't need to import this, and this doesn't need
to import core, because Python Protocols are structural. Whoever
instantiates this (eventually `web`, once Phase C wires it in) passes
it to core.optimize_day() in place of SyntheticPriceProvider.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone

from wattstack_ingestion.analysis import (
    bucket_start_for_value,
    classify_system_length,
    efa_block_number_for_hour,
    probability_by_bin,
    seasonal_average_by_period,
)
from wattstack_ingestion.elexon import ElexonClient
from wattstack_ingestion.forecasts import ElexonDemandForecastProvider
from wattstack_ingestion.neso import KNOWN_RESOURCES, NesoClient


class ElexonWholesalePriceProvider:
    """Wholesale price for wattstack_core's PriceProvider protocol --
    a seasonal average built from real historical Market Index Data
    (MID), NOT a live forecast.

    This exists specifically for triggering a day-ahead optimization
    BEFORE N2EX's day-ahead gate closure (09:50) -- at that point,
    tomorrow's wholesale price genuinely doesn't exist as settled fact
    yet, and MID itself is realised/settled data, not a prediction.
    Using it live for a future day would be wrong in the same way
    system_prices() would be: real data, wrong tense.

    What this does instead: fetch a rolling window of MID history
    ending before the target day, average by settlement period
    (seasonal_average_by_period(), which already excludes MID's known
    all-zero-value dates), and use that pooled average as the price
    for every period of the target day. A real, honest baseline -- a
    "climatological" forecast, not a predictive one. A genuinely
    predictive model (demand/wind forecasts as explanatory inputs) is
    real future work, not built here.

    Deliberately implements only `wholesale_prices()`, not the full
    `PriceProvider` protocol -- `reserve_prices()` (DC, BM) is
    separate, real future work per the roadmap's fast-path-to-v1
    scoping. Using this provider in an `optimize_day()` call where any
    reserve market is also active will raise an `AttributeError` when
    the optimizer reaches for `reserve_prices()` -- fine for a
    wholesale-only run, not yet a drop-in replacement for
    `SyntheticPriceProvider` in the full stacked optimizer.

    MID's endpoint and query parameters ARE now confirmed live (a
    real request URL was checked, correcting an earlier wrong guess --
    see ElexonClient.market_index_data()'s docstring). Also confirmed
    live: a single MID request only covers a 7-day span -- this
    provider uses market_index_data_range(), which chunks any longer
    lookback window into <=7-day requests automatically. Field names
    inside a MID row remain unconfirmed -- `period_field`/
    `price_field` below default to a reasonable guess
    (`settlementPeriod`/`price`) but are constructor parameters
    specifically so they can be corrected once verify_mid_schema()
    shows the real ones, without needing a code change.

    Real, resolved finding, confirmed live: the most recent several
    days of MID data showed zero price and volume for N2EXMIDP
    specifically, while APXMIDP for the identical dates showed real,
    non-zero data. That rules out a general MID reporting lag (which
    would affect both providers equally for the same dates) -- this
    looks like an N2EX-specific feed issue into MID. Defaults to
    APXMIDP for that reason, not because APX is "the" GB day-ahead
    exchange -- N2EX's own general market prominence turned out not
    to matter once live data showed which provider was actually
    reporting. `seasonal_average_by_period()`'s zero-exclusion still
    applies regardless of provider, in case this recurs or a different
    provider has its own gaps later.
    """

    def __init__(
        self,
        client: ElexonClient | None = None,
        lookback_days: int = 28,
        period_field: str = "settlementPeriod",
        price_field: str = "price",
        data_providers: list[str] | None = None,
    ):
        self.client = client or ElexonClient()
        self.lookback_days = lookback_days
        self.period_field = period_field
        self.price_field = price_field
        self.data_providers = data_providers if data_providers is not None else ["APXMIDP"]

    def wholesale_prices(self, day: date) -> list[float]:
        """48 half-hourly prices for `day` -- the seasonal average
        described above, not real-time or forecast data for `day`
        itself. A period with no surviving historical observations
        (all excluded as zero, or genuinely never fetched) falls back
        to 0.0 -- worth checking the returned list for exact zeros
        before trusting a specific period, since that's the fallback,
        not a real signal.

        Fetches the lookback window (`lookback_days` before `day`,
        exclusive of `day` itself -- the target day is never fetched,
        since at a pre-gate-closure trigger that data genuinely
        doesn't exist yet) via market_index_data_range(), which
        chunks into <=7-day requests as needed to respect MID's
        confirmed 7-day range limit.
        """
        to_time = datetime(day.year, day.month, day.day)
        from_time = to_time - timedelta(days=self.lookback_days)
        rows = self.client.market_index_data_range(from_time, to_time, data_providers=self.data_providers)

        averages = seasonal_average_by_period(rows, period_field=self.period_field, value_field=self.price_field)
        return [averages.get(period, 0.0) for period in range(1, 49)]


class NesoDCPriceProvider:
    """reserve_prices() for DC-High and DC-Low specifically, built
    from real historical DC auction clearing prices
    (response_reserve_results_summary, already confirmed reachable --
    see neso.py's KNOWN_RESOURCES). A seasonal average by EFA block,
    pooled across historical days -- NOT a forecast, same honesty as
    ElexonWholesalePriceProvider's MID-based one.

    Does NOT cover BM (bm_offer/bm_bid) -- reserve_prices() raises
    ValueError for any market other than DC-High/DC-Low, matching the
    roadmap's fast-path scoping (BM stays a separate, later piece:
    wind-volatility-informed reservation with a flat placeholder
    price, not this kind of real-data-averaged provider). Deliberately
    doesn't implement `wholesale_prices()` either -- pair with
    ElexonWholesalePriceProvider for a stacked run, same as the
    roadmap describes.

    Structural, not an import of core.markets.Market (ingestion
    doesn't depend on core, ADR 0009) -- dispatches on the `market`
    argument's `.name` attribute, matching Market's enum member names
    (DYNAMIC_CONTAINMENT_HIGH / DYNAMIC_CONTAINMENT_LOW) as defined in
    core/markets.py, without needing to import that module.

    Field names inside a result row are NOT independently confirmed --
    which field identifies the service (DC-High vs DC-Low) and its
    exact string values, and which field holds the clearing price.
    NESO's own EAC results page does confirm `deliveryStart`/
    `deliveryEnd` are real field names, UTC datetimes not local time --
    that part is used directly, not guessed. Everything else is a
    constructor parameter with a reasonable-guess default, same
    pattern as ElexonWholesalePriceProvider's period_field/price_field
    -- correct them once verify_schema() shows the real ones.

    Confirmed live: `auctionProduct` is the field that actually holds
    "DCH"/"DCL" -- `serviceType` is a real field too, but holds a
    broader category ("Response", "Slow Reserve") that doesn't
    distinguish DC-High from DC-Low at all. An earlier version of this
    class filtered on `service_type_field` and would have matched
    nothing for either market. `service_type_field` was removed
    entirely rather than kept alongside `auction_product_field` --
    a parameter nothing reads is worse than no parameter, since it
    looks configurable while silently doing nothing.

    Fetches via `sort=f"{delivery_start_field} desc"` specifically so
    a plain `limit` fetch returns the most recent records, not an
    arbitrary subset of nearly three years of history (this dataset
    covers November 2023 onwards) -- without an explicit sort, "most
    recent limit rows" isn't a safe assumption about what any API
    returns by default.
    """

    def __init__(
        self,
        client: NesoClient | None = None,
        lookback_days: int = 90,
        limit: int = 5000,
        delivery_start_field: str = "deliveryStart",
        auction_product_field: str = "auctionProduct",
        price_field: str = "clearingPrice",
        dc_high_value: str = "DCH",
        dc_low_value: str = "DCL",
    ):
        self.client = client or NesoClient()
        self.lookback_days = lookback_days
        self.limit = limit
        self.delivery_start_field = delivery_start_field
        self.auction_product_field = auction_product_field
        self.price_field = price_field
        self.dc_high_value = dc_high_value
        self.dc_low_value = dc_low_value

    def reserve_prices(self, day: date, market) -> list[float]:
        """48 half-hourly prices for `day` and `market` -- DC-High or
        DC-Low only, the seasonal average described above, not
        real-time or forecast data for `day` itself. Raises
        ValueError for any other market rather than silently returning
        something wrong.

        DC clears per EFA block (6 per day), not per settlement
        period (48) -- each EFA block's average price is broadcast
        across the 8 settlement periods it covers, derived via
        efa_block_number_for_hour(), not assumed to already align with
        settlement-period boundaries.
        """
        market_name = getattr(market, "name", str(market))
        if market_name == "DYNAMIC_CONTAINMENT_HIGH":
            target_value = self.dc_high_value
        elif market_name == "DYNAMIC_CONTAINMENT_LOW":
            target_value = self.dc_low_value
        else:
            raise ValueError(f"NesoDCPriceProvider only covers DC-High/DC-Low, got {market_name!r}")

        rows = self.client.datastore_search(
            KNOWN_RESOURCES["response_reserve_results_summary"],
            limit=self.limit,
            sort=f"{self.delivery_start_field} desc",
        )

        cutoff = datetime(day.year, day.month, day.day)
        lookback_start = cutoff - timedelta(days=self.lookback_days)

        efa_rows = []
        for row in rows:
            if row.get(self.auction_product_field) != target_value:
                continue
            raw_start = row.get(self.delivery_start_field)
            price = row.get(self.price_field)
            if raw_start is None or price is None:
                continue
            try:
                parsed = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
            if not (lookback_start <= parsed < cutoff):
                continue
            efa_rows.append({"efa_block": efa_block_number_for_hour(parsed.hour), "price": price})

        averages = seasonal_average_by_period(efa_rows, period_field="efa_block", value_field="price")

        result = []
        for period in range(1, 49):
            hour = (period - 1) // 2
            efa_block = efa_block_number_for_hour(hour)
            result.append(averages.get(efa_block, 0.0))
        return result


class ElexonBMPriceProvider:
    """reserve_prices() for BM-Offer and BM-Bid, built from real
    historical Bid-Offer Data (BOD) submitted price levels -- NOT a
    forecast, same honesty as the wholesale/DC providers, but a
    genuinely bigger approximation than either.

    Real, important limitation, stated plainly rather than glossed
    over: BM is pay-as-bid, not pay-as-clear. Unlike MID or DC's
    clearing price, there is no single "the BM price" for a given
    period -- every accepted bid/offer is paid its own submitted
    price. This provider averages *submitted* price levels across all
    BM units (BOD -- confirmed price-bearing, per ADR 0006; BOALF
    carries volume/timing, not price), which reflects "what units were
    willing to trade at," not "what typically gets paid." A genuinely
    calibrated acceptance-weighted price (BOD joined with BOALF
    acceptance data) is real future work, not built here.

    `MarketSpec`'s own docstring (markets.py) already states BM's
    reserve price needs to be "an expected-value proxy... already
    probability-weighted" -- `acceptance_derating` applies that
    directly: expected_price = average_submitted_price *
    acceptance_derating. The default (0.3) is a stated, conservative
    placeholder, not calibrated against real acceptance-rate data --
    correcting it with a real figure is exactly the acceptance-risk
    work already on the roadmap, deliberately deferred for v1.

    Not filtered to battery units specifically -- averages across all
    BM units submitting bids/offers, on the reasoning that the
    question is "what's the general BM opportunity level," not "what
    do batteries specifically get paid." Worth reconsidering if that
    turns out to matter.

    Field names inside a BOD row are NOT independently confirmed --
    same treatment as every other unconfirmed schema in this project,
    constructor parameters with reasoned-guess defaults, correctable
    once a real response is checked.

    Real cost consideration, different from wholesale/DC: BOD has no
    bulk date-range endpoint (bid_offer_data_for_day() costs 48
    requests per day, the same shape as B1610's correction). Defaults
    to a much shorter lookback (7 days) than DC's (90) for that
    reason -- a longer lookback here gets expensive fast.
    """

    def __init__(
        self,
        client: ElexonClient | None = None,
        lookback_days: int = 7,
        period_field: str = "settlementPeriod",
        offer_price_field: str = "offerPrice",
        bid_price_field: str = "bidPrice",
        acceptance_derating: float = 0.3,
    ):
        self.client = client or ElexonClient()
        self.lookback_days = lookback_days
        self.period_field = period_field
        self.offer_price_field = offer_price_field
        self.bid_price_field = bid_price_field
        self.acceptance_derating = acceptance_derating

    def reserve_prices(self, day: date, market) -> list[float]:
        """48 half-hourly prices for `day` and `market` -- BM-Offer or
        BM-Bid only. Raises ValueError for any other market rather
        than silently returning something wrong.

        Structural, not an import of core.markets.Market (ADR 0009),
        same pattern as NesoDCPriceProvider -- dispatches on
        `market.name`.
        """
        market_name = getattr(market, "name", str(market))
        if market_name == "BM_OFFER":
            price_field = self.offer_price_field
        elif market_name == "BM_BID":
            price_field = self.bid_price_field
        else:
            raise ValueError(f"ElexonBMPriceProvider only covers BM_OFFER/BM_BID, got {market_name!r}")

        rows: list[dict] = []
        for offset in range(1, self.lookback_days + 1):
            history_day = day - timedelta(days=offset)
            rows.extend(self.client.bid_offer_data_for_day(history_day))

        averages = seasonal_average_by_period(rows, period_field=self.period_field, value_field=price_field)
        return [averages.get(period, 0.0) * self.acceptance_derating for period in range(1, 49)]


class ElexonImbalancePriceProvider:
    """reserve_prices() for BM-Offer and BM-Bid, built from a genuine
    predictive model -- a demand-probability-weighted mixture of
    conditional System Price distributions. Built from two real
    sources (Timera Energy's "the risk is in the distribution, not
    the mean"; Browell & Gilbert, Energies 2022) and validated in
    `notebooks/imbalance_price_probabilistic_forecast.py` before being
    promoted here -- see `docs/adr/0020`, `docs/adr/0021`.

    Supersedes `ElexonBMPriceProvider`'s approach: targets System
    Price directly (the real imbalance settlement price, derived from
    the marginal action actually taken to balance the system), not
    submitted BOD price levels. `ElexonBMPriceProvider` is kept in
    this module, not deleted -- a known, tested alternative, no longer
    the recommended default.

    Validated against real GB data (60-day training window, 12-day
    chronological holdout): 2.3% MAE improvement over the flat
    seasonal-average baseline -- close to Browell & Gilbert's own
    published 3% day-ahead result, a genuine match to peer-reviewed
    literature on real data, not just a synthetic demonstration.

    No shrinkage applied, deliberately. The original bad real-data
    result (£22.22/MWh MAE, worse than the £5.67/MWh flat baseline)
    was diagnosed as a training window that was far too small (1-2
    days) -- once trained on a realistic amount of real data, the raw
    empirical `probability_by_bin()` performed best; shrinkage wasn't
    the actual fix, more data was. `shrink_probability_by_bin()`
    remains available in `analysis.py` for anyone using a much
    shorter lookback in the future, but isn't used here.

    Real, principled asymmetry between the two markets, not a shared
    blended forecast: `BM_OFFER` (discharge) is only genuinely valuable
    when the system is Short (NESO needs more generation, not less);
    `BM_BID` (charge) only when Long. Each market's forecast is
    `P(its own regime) x mean price in that regime` -- e.g. `BM_OFFER`
    gets `P(Short) x mean_price_given_short`, with an implicit ~0
    contribution from Long periods, since discharge capacity held
    during a Long period generally isn't what NESO is calling for.
    This is a real economic modelling choice, not just a statistical
    convenience -- worth revisiting if real acceptance data later
    shows meaningful off-regime value for either market, but not
    assumed here without evidence.

    Real cost consideration: training needs `lookback_days` days of
    BOTH demand-forecast history (one call each, via
    `ElexonDemandForecastProvider.as_of()`) AND system prices (one
    call each, via `system_prices()`) -- roughly `2 * lookback_days`
    requests per `reserve_prices()` call, before caching. The default
    (60 days, matching what was validated) costs ~120 requests.

    Field names default to what was validated against real data in
    the notebook (`settlementPeriod`, `netImbalanceVolume`) but
    `price_field` (`systemSellPrice`) and `demand_field` (`demand`)
    are still constructor-parameter guesses for anyone whose real
    response shapes differ -- correctable without a code change.
    """

    def __init__(
        self,
        client: ElexonClient | None = None,
        forecast_provider: ElexonDemandForecastProvider | None = None,
        lookback_days: int = 60,
        demand_bin_width: float = 1000.0,
        period_field: str = "settlementPeriod",
        demand_field: str = "transmissionSystemDemand",
        price_field: str = "systemSellPrice",
        niv_field: str = "netImbalanceVolume",
    ):
        self.client = client or ElexonClient()
        self.forecast_provider = forecast_provider or ElexonDemandForecastProvider(client=self.client)
        self.lookback_days = lookback_days
        self.demand_bin_width = demand_bin_width
        self.period_field = period_field
        self.demand_field = demand_field
        self.price_field = price_field
        self.niv_field = niv_field

    def _trigger_time(self, target_day: date) -> datetime:
        """10:00 UTC the day before `target_day` -- the same
        day-ahead trigger convention used throughout this project
        (ADR 0009), not reinvented here."""
        return datetime(target_day.year, target_day.month, target_day.day, tzinfo=timezone.utc) - timedelta(hours=14)

    def reserve_prices(self, day: date, market) -> list[float]:
        """48 half-hourly prices for `day` and `market` -- BM-Offer or
        BM-Bid only. Raises ValueError for any other market rather
        than silently returning something wrong.

        Each market gets its own regime-specific forecast, not a
        shared blend: `P(Short) x mean_price_given_short` for
        `BM_OFFER`, `P(Long) x mean_price_given_long` for `BM_BID` --
        see the class docstring for the economic reasoning. Both
        markets share the same trained P(Short|bucket) table and
        conditional means; only the final per-market formula differs.

        Structural, not an import of core.markets.Market (ADR 0009),
        same pattern as every other reserve provider here -- dispatches
        on `market.name`.
        """
        market_name = getattr(market, "name", str(market))
        if market_name not in ("BM_OFFER", "BM_BID"):
            raise ValueError(f"ElexonImbalancePriceProvider only covers BM_OFFER/BM_BID, got {market_name!r}")

        # 1. Fetch and join a lookback_days window of (forecast demand, realised price, system length).
        joined: list[dict] = []
        for offset in range(1, self.lookback_days + 1):
            history_day = day - timedelta(days=offset)
            demand_rows = self.forecast_provider.as_of(self._trigger_time(history_day))
            price_rows = {r.get(self.period_field): r for r in self.client.system_prices(history_day)}

            for demand_row in demand_rows:
                price_row = price_rows.get(demand_row.get(self.period_field))
                if price_row is None:
                    continue

                demand = demand_row.get(self.demand_field)
                price = price_row.get(self.price_field)
                niv = price_row.get(self.niv_field)
                if demand is None or price is None or niv is None:
                    continue
                joined.append({
                    "demand": float(demand), "price": float(price),
                    "system_length": classify_system_length(float(niv)),
                })

        # 2. Train: P(Short|demand bucket), and mean price conditional on system length.
        probability_table = probability_by_bin(
            [r["demand"] for r in joined], [r["system_length"] for r in joined], bin_width=self.demand_bin_width
        )
        short_prices = [r["price"] for r in joined if r["system_length"] == "Short"]
        long_prices = [r["price"] for r in joined if r["system_length"] == "Long"]
        mean_short = statistics.mean(short_prices) if short_prices else None
        mean_long = statistics.mean(long_prices) if long_prices else None
        # overall (non-bucket-specific) rate -- the fallback when a period's own demand
        # bucket has no training data, so the fallback still respects the same per-market
        # asymmetry rather than reverting to an undifferentiated blend.
        overall_p_short = len(short_prices) / len(joined) if joined else 0.0
        overall_p_long = 1.0 - overall_p_short if joined else 0.0

        # 3. Predict: the target day's own forecast demand, per period.
        target_demand_rows = self.forecast_provider.as_of(self._trigger_time(day))
        target_demand_by_period = {r.get(self.period_field): r.get(self.demand_field) for r in target_demand_rows}
        available_buckets = list(probability_table.keys())

        result = []
        for period in range(1, 49):
            demand = target_demand_by_period.get(period)
            bucket = bucket_start_for_value(float(demand), self.demand_bin_width, available_buckets) if demand is not None else None
            if bucket is not None:
                probs = probability_table[bucket]
                p_short = probs.get("Short", 0.0)
                p_long = probs.get("Long", 0.0)
            else:
                p_short, p_long = overall_p_short, overall_p_long

            if market_name == "BM_OFFER":
                forecast = p_short * mean_short if mean_short is not None else 0.0
            else:  # BM_BID
                forecast = p_long * mean_long if mean_long is not None else 0.0
            result.append(forecast)
        return result


class CombinedPriceProvider:
    """Composes a wholesale provider and one or more reserve-market
    providers into one object satisfying core's full `PriceProvider`
    protocol (`wholesale_prices()` + `reserve_prices()`) --
    `optimize_day()` takes a single price_provider argument, not one
    per market, so running a stacked optimization needs something
    that answers both.

    `reserve_providers` is a list, not a single provider (a breaking
    change from this class's first version, made before any real
    external caller depended on the old shape) -- each provider
    already self-declares which markets it covers by raising
    `ValueError` for anything else (`NesoDCPriceProvider` for DC-High/
    DC-Low, `ElexonBMPriceProvider` for BM-Offer/BM-Bid). Trying each
    in turn and catching that `ValueError` is how routing works here,
    rather than a market-to-provider mapping this class would need to
    keep in sync by hand.

    Deliberately a thin delegator, not a merge of any provider's
    logic -- each specialised provider stays independently simple and
    independently testable; this just routes each protocol method to
    the right one.
    """

    def __init__(self, wholesale_provider, reserve_providers: list):
        self.wholesale_provider = wholesale_provider
        self.reserve_providers = reserve_providers

    def wholesale_prices(self, day: date) -> list[float]:
        return self.wholesale_provider.wholesale_prices(day)

    def reserve_prices(self, day: date, market) -> list[float]:
        for provider in self.reserve_providers:
            try:
                return provider.reserve_prices(day, market)
            except ValueError:
                continue
        market_name = getattr(market, "name", str(market))
        raise ValueError(f"No reserve provider in this CombinedPriceProvider covers market {market_name!r}")
