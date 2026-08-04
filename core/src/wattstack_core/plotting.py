"""Dispatch visualisation: turn one day's DispatchResult into a
multi-panel figure -- SOC and its bounds, headroom/footroom, charge
and discharge power, reserve capacity by market, and the prices that
drove all of it. This is what makes the SOC-linked co-optimization
this project is built around actually visible, not just correct.

Headroom and footroom, as plotted here, are derived purely from the
SOC trajectory and the battery's bounds:
  headroom_mwh[t] = soc_mwh[t]        - soc_min_mwh   (room to discharge further)
  footroom_mwh[t] = soc_max_mwh       - soc_mwh[t]     (room to charge further)
These are distinct from the headroom/footroom constraint expressions
inside optimizer.py, which are the energy a *committed* reserve
product needs held back to be deliverable -- see docs/adr/0002 and
markets.py. The two are related (the constraints are what keep
headroom/footroom from going negative) but are not the same quantity.

Needs plotly, which core does NOT depend on by default -- install
`wattstack-core[plotting]` to use this module. Kept separate so a
plain backtest/CLI install stays lightweight.
"""
from __future__ import annotations

from wattstack_core.battery import BatterySpec
from wattstack_core.markets import MARKET_REGISTRY, market_display_name
from wattstack_core.prices import PERIODS_PER_DAY
from wattstack_core.results import DispatchResult

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "wattstack_core.plotting needs plotly -- install with `pip install wattstack-core[plotting]`"
    ) from exc

_MARKET_COLORS = {
    "dynamic_containment_high": "#eb6834",
    "dynamic_containment_low": "#1baf7a",
    "bm_offer": "#eda100",
    "bm_bid": "#7b61ff",
}


_SETTLEMENT_UNIT_LABELS = {"per_mwh": "GBP/MWh", "per_mw_h": "GBP/MW/h"}


def _period_labels() -> list[str]:
    return [f"{t // 2:02d}:{'00' if t % 2 == 0 else '30'}" for t in range(PERIODS_PER_DAY)]


def dispatch_figure(result: DispatchResult, battery: BatterySpec) -> "go.Figure":
    """Build the multi-panel dispatch figure for a single optimised day.

    Panels, top to bottom, all sharing a time-of-day x-axis:
      1. State of charge, with the battery's usable min/max bounds
      2. Headroom and footroom -- the two quantities that actually
         limit how much reserve capacity can be committed, in either
         direction
      3. Charge / discharge power
      4. Reserve capacity committed, by market
      5. Prices that drove the decision (only if the result carries
         them -- see results.DispatchResult.wholesale_price)
    """
    x = _period_labels()
    active_reserve = [m for m in MARKET_REGISTRY if m in result.reserve_mw]

    headroom = [round(soc - battery.soc_min_mwh, 4) for soc in result.soc_mwh]
    footroom = [round(battery.soc_max_mwh - soc, 4) for soc in result.soc_mwh]

    has_prices = bool(result.wholesale_price) or bool(result.reserve_price)
    row_titles = [
        "State of charge (MWh)",
        "Headroom & footroom (MWh)",
        "Power (MW)",
        "Reserve capacity (MW)",
    ]
    if has_prices:
        row_titles.append("Prices (GBP)")
    n_rows = len(row_titles)

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=row_titles,
    )

    # Panel 1: SOC and its bounds
    fig.add_trace(
        go.Scatter(x=x, y=result.soc_mwh, name="SOC", line=dict(color="#2a78d6", width=2)), row=1, col=1
    )
    fig.add_hline(y=battery.soc_min_mwh, line=dict(color="#898781", dash="dash", width=1), row=1, col=1)
    fig.add_hline(y=battery.soc_max_mwh, line=dict(color="#898781", dash="dash", width=1), row=1, col=1)

    # Panel 2: headroom / footroom
    fig.add_trace(
        go.Scatter(x=x, y=headroom, name="Headroom (can discharge)", line=dict(color="#1baf7a")), row=2, col=1
    )
    fig.add_trace(
        go.Scatter(x=x, y=footroom, name="Footroom (can charge)", line=dict(color="#eb6834")), row=2, col=1
    )

    # Panel 3: charge / discharge, plotted on opposite sides of zero
    fig.add_trace(
        go.Bar(x=x, y=[-c for c in result.charge_mw], name="Charge", marker_color="#2a78d6"), row=3, col=1
    )
    fig.add_trace(
        go.Bar(x=x, y=result.discharge_mw, name="Discharge", marker_color="#eda100"), row=3, col=1
    )

    # Panel 4: reserve capacity by market, stacked
    for m in active_reserve:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=result.reserve_mw[m],
                name=market_display_name(m),
                stackgroup="reserve",
                line=dict(width=0.5, color=_MARKET_COLORS.get(m.value, "#898781")),
            ),
            row=4,
            col=1,
        )

    # Panel 5: prices, if the result carries them
    if has_prices:
        if result.wholesale_price:
            fig.add_trace(
                go.Scatter(
                    x=x, y=result.wholesale_price, name="Wholesale price (GBP/MWh)", line=dict(color="#2a78d6")
                ),
                row=5,
                col=1,
            )
        for m, series in result.reserve_price.items():
            unit = _SETTLEMENT_UNIT_LABELS[MARKET_REGISTRY[m].settlement_unit]
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=series,
                    name=f"{market_display_name(m)} price ({unit})",
                    line=dict(color=_MARKET_COLORS.get(m.value, "#898781"), dash="dot"),
                ),
                row=5,
                col=1,
            )

    fig.update_layout(
        height=210 * n_rows,
        margin=dict(l=60, r=20, t=60, b=40),
        barmode="relative",
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="left", x=0),
        title=f"Dispatch \u2014 {result.day.isoformat()}",
    )
    fig.update_xaxes(nticks=12)
    return fig
