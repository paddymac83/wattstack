"""Exploratory plots over raw GB market data.

Deliberately generic: every function here takes plain timestamps and
numbers, not Elexon or NESO response objects. That means these are
useful for ANY time series you pull in later (a new dataset, a new
API), and they're fully testable against synthetic data with zero
network dependency -- see tests/test_plots.py.

The five views here answer the questions that actually matter when
you're deciding what a feature should do: how volatile is this price,
what's its shape across the day and across the week, and how does it
relate to another price series you might be co-optimizing against.
"""
from __future__ import annotations

from datetime import datetime

import plotly.graph_objects as go


def price_timeseries(timestamps: list[datetime], values: list[float], title: str, y_label: str) -> go.Figure:
    """The rawest view: what did this price actually do over the
    fetched window. Start here -- spikes and gaps you notice here are
    what the other four plots go on to explain."""
    fig = go.Figure(go.Scatter(x=timestamps, y=values, mode="lines", line=dict(color="#2a78d6")))
    fig.update_layout(title=title, yaxis_title=y_label, margin=dict(l=60, r=20, t=50, b=40))
    return fig


def price_distribution(values: list[float], title: str, x_label: str, bins: int = 40) -> go.Figure:
    """Shape of the distribution -- how fat is the right tail, is
    there real mass at or below zero. Matters directly for deciding
    whether your battery model needs to handle negative prices."""
    fig = go.Figure(go.Histogram(x=values, nbinsx=bins, marker_color="#1baf7a"))
    fig.update_layout(title=title, xaxis_title=x_label, yaxis_title="Count", margin=dict(l=60, r=20, t=50, b=40))
    return fig


def price_by_hour_of_day(timestamps: list[datetime], values: list[float], title: str, y_label: str) -> go.Figure:
    """Diurnal shape as a box plot per hour -- this is the thing that
    determines when a battery actually wants to charge and discharge,
    and how much the pattern varies day to day rather than being a
    fixed curve (the synthetic price provider in core assumes a fixed
    shape; this tells you how wrong that assumption really is)."""
    by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
    for ts, v in zip(timestamps, values):
        by_hour[ts.hour].append(v)
    fig = go.Figure()
    for hour in range(24):
        if by_hour[hour]:
            fig.add_trace(go.Box(y=by_hour[hour], x=[hour] * len(by_hour[hour]), name=str(hour), marker_color="#eb6834"))
    fig.update_layout(title=title, xaxis_title="Hour of day", yaxis_title=y_label, showlegend=False, margin=dict(l=60, r=20, t=50, b=40))
    return fig


def price_by_weekday(timestamps: list[datetime], values: list[float], title: str, y_label: str) -> go.Figure:
    """Same idea, grouped by day of week -- worth knowing before
    assuming a Tuesday looks like a Saturday."""
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_day: dict[int, list[float]] = {d: [] for d in range(7)}
    for ts, v in zip(timestamps, values):
        by_day[ts.weekday()].append(v)
    fig = go.Figure()
    for d in range(7):
        if by_day[d]:
            fig.add_trace(go.Box(y=by_day[d], x=[day_names[d]] * len(by_day[d]), name=day_names[d], marker_color="#eda100"))
    fig.update_layout(title=title, xaxis_title="Day of week", yaxis_title=y_label, showlegend=False, margin=dict(l=60, r=20, t=50, b=40))
    return fig


def overlay_two_series(
    timestamps: list[datetime],
    values_a: list[float],
    values_b: list[float],
    name_a: str,
    name_b: str,
    title: str,
    y_label: str,
) -> go.Figure:
    """Two price series on the same time axis -- the direct way to
    see whether wholesale and a response market actually peak at
    different times (real revenue-stacking opportunity) or move
    together (less to gain from splitting capacity between them)."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=timestamps, y=values_a, name=name_a, line=dict(color="#2a78d6")))
    fig.add_trace(go.Scatter(x=timestamps, y=values_b, name=name_b, line=dict(color="#eb6834")))
    fig.update_layout(title=title, yaxis_title=y_label, margin=dict(l=60, r=20, t=50, b=40), legend=dict(orientation="h", y=1.1))
    return fig


def grouped_bar_chart(
    categories: list[str],
    series: dict[str, list[float]],
    title: str,
    x_label: str,
    y_label: str,
) -> go.Figure:
    """A bar chart with one set of bars per category, grouped side by
    side per named series -- e.g. count of settlement periods per
    GBP20/MWh price bin, one bar for Long and one for Short at each
    bin. Generic on purpose: useful well beyond this one chart."""
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
    fig = go.Figure()
    for i, (name, values) in enumerate(series.items()):
        fig.add_trace(go.Bar(x=categories, y=values, name=name, marker_color=colors[i % len(colors)]))
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        barmode="group",
        margin=dict(l=60, r=20, t=50, b=90),
        legend=dict(orientation="h", y=1.1),
    )
    fig.update_xaxes(tickangle=-45)
    return fig


def stacked_bar_chart(
    categories: list[str],
    series: dict[str, list[float]],
    title: str,
    x_label: str,
    y_label: str,
) -> go.Figure:
    """A stacked bar chart -- one bar per category (x-axis), each
    built from segments in `series` -- e.g. daily accepted offer
    volume, stacked by fuel type including BSAA as its own segment."""
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#898781", "#e87ba4", "#7b61ff"]
    fig = go.Figure()
    for i, (name, values) in enumerate(series.items()):
        fig.add_trace(go.Bar(x=categories, y=values, name=name, marker_color=colors[i % len(colors)]))
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        barmode="stack",
        margin=dict(l=60, r=20, t=50, b=90),
        legend=dict(orientation="h", y=1.1),
    )
    fig.update_xaxes(tickangle=-45)
    return fig


def spread_chart(
    categories: list[str],
    means: list[float],
    std_devs: list[float],
    counts: list[int],
    title: str,
    x_label: str,
    y_label: str,
) -> go.Figure:
    """Mean with error bars (+/- one standard deviation) per category
    -- built for spread_by_bin()'s output. This shows dispersion, not
    a count -- the right chart for "does this variable predict
    volatility," as distinct from grouped_bar_chart's "does this
    variable predict which group wins."

    Sample size is shown in hover text, not just implied -- a wide
    error bar on a bucket with 2 observations means something very
    different from the same width on a bucket with 200."""
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=categories,
            y=means,
            error_y=dict(type="data", array=std_devs, visible=True),
            marker_color="#2a78d6",
            customdata=counts,
            hovertemplate="%{x}<br>mean: %{y}<br>n: %{customdata}<extra></extra>",
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        margin=dict(l=60, r=20, t=50, b=90),
        showlegend=False,
    )
    fig.update_xaxes(tickangle=-45)
    return fig
