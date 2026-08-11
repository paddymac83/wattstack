"""Real price providers, structurally compatible with wattstack_core's
PriceProvider protocol -- same placement reasoning as forecasts.py
(ADR 0009): core doesn't need to import this, and this doesn't need
to import core, because Python Protocols are structural. Whoever
instantiates this (eventually `web`, once Phase C wires it in) passes
it to core.optimize_day() in place of SyntheticPriceProvider.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from wattstack_ingestion.analysis import seasonal_average_by_period
from wattstack_ingestion.elexon import ElexonClient


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
