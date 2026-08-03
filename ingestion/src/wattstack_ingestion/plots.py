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
