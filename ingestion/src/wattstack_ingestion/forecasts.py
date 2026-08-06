"""Vintage-aware forecast access.

ForecastProvider is the seam between anything that needs to know
"what was known at time X" and wherever that forecast data actually
comes from -- mirrors core's PriceProvider protocol conceptually
(same idea: an abstract interface a live run and a backtest can both
call identically), but lives here in ingestion, not in core.

That placement is deliberate, not an oversight: core's optimizer
doesn't consume forecasts directly yet, only prices. Putting this
protocol in core would mean designing an abstraction ahead of an
actual consumer. Python Protocols are structural (duck-typed) --
ElexonDemandForecastProvider satisfies core's eventual needs without
either package importing the other. See docs/adr/0009.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from wattstack_ingestion.elexon import ElexonClient


@runtime_checkable
class ForecastProvider(Protocol):
    def as_of(self, publish_time: datetime) -> list[dict]:
        """Whatever this provider forecasts, as it was known at
        publish_time.

        A live run calls as_of(now); a backtest calls
        as_of(historical_trigger_time) for each historical day. Same
        code path either way -- deliberately no special-casing "now"
        -- which is what makes a backtest built on this trustworthy
        rather than a parallel simulation that can quietly drift from
        what actually runs.
        """
        ...


class ElexonDemandForecastProvider:
    """Day-ahead national demand forecast (NDF/TSDF), vintage-aware.

    UNVERIFIED against live traffic, same status as the rest of this
    package: the endpoint path and the publishTime query parameter
    are confirmed directly from Elexon's own API client source (see
    elexon.py's demand_forecast_day_ahead_history() docstring), but
    the response's actual field names are not. Before relying on the
    *content* of what as_of() returns, run
    ElexonClient.verify_demand_forecast_schema() and look at the real
    keys -- this class only proves the vintage-retrieval *mechanism*
    works, not that downstream code knows what to do with the result
    yet.
    """

    def __init__(self, client: ElexonClient | None = None):
        self.client = client or ElexonClient()

    def as_of(self, publish_time: datetime) -> list[dict]:
        return self.client.demand_forecast_day_ahead_history(publish_time)
