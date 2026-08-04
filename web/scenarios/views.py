"""Two views: `index` renders the form (a GET), `recompute` runs the
backtest and returns just the results partial (a POST, called by
HTMX on every form change). Splitting it this way is what lets the
chart update without a full page reload -- see templates/scenarios/run.html.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.shortcuts import render

from wattstack_core.backtest import run_backtest
from wattstack_core.battery import BatterySpec
from wattstack_core.markets import Market, market_display_name
from wattstack_core.scenario import BacktestWindow, Scenario

from .forms import ScenarioForm
from .models import BacktestRunRecord, ScenarioRecord


def index(request):
    form = ScenarioForm(
        initial={"markets": [Market.WHOLESALE.value, Market.DYNAMIC_CONTAINMENT_LOW.value, Market.DYNAMIC_CONTAINMENT_HIGH.value]}
    )
    return render(request, "scenarios/run.html", {"form": form})


def recompute(request):
    form = ScenarioForm(request.POST)
    context = {}
    if form.is_valid():
        context["result"] = _run_and_save(form)
    else:
        context["errors"] = form.errors
    return render(request, "scenarios/partials/revenue_stack.html", context)


def _run_and_save(form: ScenarioForm) -> dict:
    cleaned = form.cleaned_data
    scenario = Scenario(
        name="web-session",
        battery=BatterySpec(
            power_mw=cleaned["power_mw"],
            duration_hours=cleaned["duration_hours"],
            round_trip_efficiency=cleaned["round_trip_efficiency"],
        ),
        markets=[Market(m) for m in cleaned["markets"]],
        backtest=BacktestWindow(
            start=date(2025, 1, 1),
            end=date(2025, 1, 1) + timedelta(days=cleaned["days"] - 1),
        ),
    )
    result = run_backtest(scenario)
    per_mw = result.revenue_per_mw_year(scenario.battery.power_mw)

    record = ScenarioRecord.objects.create(name=scenario.name, config=scenario.model_dump(mode="json"))
    BacktestRunRecord.objects.create(
        scenario=record,
        revenue_by_market={m.value: v for m, v in result.revenue_by_market.items()},
        total_revenue=result.total_revenue,
        revenue_per_mw_year=per_mw,
    )

    # Clamp rather than error if "day to inspect" outruns a shortened backtest.
    inspect_index = min(max(cleaned["inspect_day"], 1), len(result.days)) - 1
    dispatch_day = result.days[inspect_index]

    return {
        "per_mw": round(per_mw),
        "total_revenue": round(result.total_revenue),
        "n_days": len(result.days),
        "inspect_day": inspect_index + 1,
        "chart_html": _revenue_stack_chart(result, scenario.battery.power_mw),
        "dispatch_chart_html": _dispatch_chart(dispatch_day, scenario.battery),
    }


def _revenue_stack_chart(result, power_mw: float) -> str:
    import plotly.graph_objects as go

    n_days = max(len(result.days), 1)
    labels, values = [], []
    for market, value in result.revenue_by_market.items():
        if value:
            labels.append(market_display_name(market))
            values.append(round(value / n_days * 365 / power_mw))

    if not values:
        labels, values = ["No revenue"], [0]

    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h"))
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=30),
        height=200,
        xaxis_title="GBP/MW/yr",
        showlegend=False,
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def _dispatch_chart(dispatch_day, battery) -> str:
    from wattstack_core.plotting import dispatch_figure

    fig = dispatch_figure(dispatch_day, battery)
    # include_plotlyjs=False: the revenue-stack chart above this one
    # in the same response already loads Plotly.js from CDN. Both
    # charts render in one HTMX swap, in this fixed order, so the
    # library is guaranteed loaded before this chart's script runs.
    return fig.to_html(full_html=False, include_plotlyjs=False)
