"""Plotly figures for the dashboard, following one validated palette and mark spec.

Palette (validated with the dataviz skill's validate_palette.js on 2026-10-19,
light and dark, all checks passing):
- Ordinal tier ramp (SMB -> Mid-Market -> Enterprise): one blue hue, light to dark.
  Tiers are ordered, so they get a ramp, not unrelated categorical hues.
- Two-series categorical (baseline vs scenario): blue, orange.
- Diverging polarity (ahead of quota vs shortfall): blue vs red poles.

Marks: thin bars with rounded data-ends, a small gap between bars, hairline
solid gridlines, no dual axes, and a hover tooltip on every mark. Every chart
in the app also has a table view.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

PALETTES = {
    "light": {
        "tiers": {"SMB": "#86b6ef", "Mid-Market": "#2a78d6", "Enterprise": "#104281"},
        "series": ["#2a78d6", "#eb6834"],
        "ahead": "#2a78d6",
        "short": "#e34948",
        "ink": "#0b0b0b",
        "ink_secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
    },
    "dark": {
        "tiers": {"SMB": "#9ec5f4", "Mid-Market": "#3987e5", "Enterprise": "#184f95"},
        "series": ["#3987e5", "#d95926"],
        "ahead": "#3987e5",
        "short": "#e66767",
        "ink": "#f0efec",
        "ink_secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
    },
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def money(value: float) -> str:
    """Compact USD: $1.37M, $212k, $950."""
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1e6:
        return f"{sign}${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.0f}k"
    return f"{sign}${v:,.0f}"


def _base_layout(fig: go.Figure, mode: str, height: int, **axes) -> go.Figure:
    p = PALETTES[mode]
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=16, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=p["ink_secondary"]),
        barcornerradius=4,
        bargap=0.25,
        bargroupgap=0.08,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title_text="",
                    font=dict(color=p["ink_secondary"])),
        hoverlabel=dict(font=dict(family=FONT)),
    )
    # automargin: let Plotly grow the margins to fit tick labels and titles instead of clipping them.
    grid = dict(gridcolor=p["grid"], gridwidth=1, zeroline=False, linecolor=p["axis"], automargin=True,
                tickfont=dict(color=p["muted"]), title_font=dict(color=p["ink_secondary"]))
    fig.update_xaxes(**grid, **axes.get("x", {}))
    fig.update_yaxes(**grid, **axes.get("y", {}))
    return fig


def funnel_reach(funnel: pd.DataFrame, mode: str) -> go.Figure:
    """Share of created deals that reach each stage, grouped by tier."""
    p = PALETTES[mode]
    fig = go.Figure()
    for tier, rows in funnel.groupby("account_tier", observed=True):
        fig.add_bar(
            name=tier, x=rows["stage_name"], y=rows["pct_of_created_reaching_stage"],
            marker_color=p["tiers"][tier],
            customdata=rows[["deals_entered", "conversion_rate"]],
            hovertemplate=(f"<b>{tier}</b> · %{{x}}<br>Reached stage: %{{y:.0%}} of created deals"
                           "<br>Deals entered: %{customdata[0]:,}<br>Converted onward: %{customdata[1]:.0%}"
                           "<extra></extra>"),
        )
    return _base_layout(fig, mode, 320, y={"tickformat": ".0%", "range": [0, 1.02], "title": "Share of created deals"},
                        x={"showgrid": False})


def stage_duration(funnel: pd.DataFrame, mode: str) -> go.Figure:
    """Average days a deal spends in each stage, by tier (resolved stage visits)."""
    p = PALETTES[mode]
    fig = go.Figure()
    for tier, rows in funnel.groupby("account_tier", observed=True):
        fig.add_bar(
            name=tier, x=rows["stage_name"], y=rows["avg_days_in_stage"],
            marker_color=p["tiers"][tier],
            customdata=rows[["median_days_in_stage"]],
            hovertemplate=(f"<b>{tier}</b> · %{{x}}<br>Average: %{{y:.1f}} days"
                           "<br>Median: %{customdata[0]:.0f} days<extra></extra>"),
        )
    return _base_layout(fig, mode, 320, y={"title": "Average days in stage"}, x={"showgrid": False})


def stalled_by_rep(reps: pd.DataFrame, mode: str) -> go.Figure:
    """Pipeline stuck in Negotiation past the stall threshold, per rep (single series)."""
    p = PALETTES[mode]
    rows = reps[reps["stalled_deal_count"] > 0].sort_values("stalled_pipeline_amount")
    fig = go.Figure(go.Bar(
        x=rows["stalled_pipeline_amount"], y=rows["rep_name"], orientation="h",
        marker_color=p["series"][0],
        customdata=rows[["stalled_deal_count", "rep_segment"]],
        hovertemplate=("<b>%{y}</b> (%{customdata[1]})<br>Stalled pipeline: $%{x:,.0f}"
                       "<br>Stalled deals: %{customdata[0]}<extra></extra>"),
    ))
    return _base_layout(fig, mode, max(240, 22 * len(rows) + 60),
                        x={"tickprefix": "$", "tickformat": "~s", "title": "Stalled pipeline (unweighted)"},
                        y={"showgrid": False})


def revenue_gap_by_rep(reps: pd.DataFrame, mode: str, basis: str = "crm") -> go.Figure:
    """Expected revenue gap per rep: right of zero = projected shortfall, left = ahead of quota."""
    p = PALETTES[mode]
    col = "expected_revenue_gap" if basis == "crm" else "expected_revenue_gap_historical"
    rows = reps.sort_values(col)
    colors = [p["short"] if v > 0 else p["ahead"] for v in rows[col]]
    fig = go.Figure(go.Bar(
        x=rows[col], y=rows["rep_name"], orientation="h", marker_color=colors,
        customdata=rows[["rep_segment", "quarterly_quota", "won_qtd"]],
        hovertemplate=("<b>%{y}</b> (%{customdata[0]})<br>Gap: $%{x:,.0f}"
                       "<br>Quarterly quota: $%{customdata[1]:,.0f}<br>Won QTD: $%{customdata[2]:,.0f}"
                       "<extra></extra>"),
    ))
    fig.add_vline(x=0, line_width=1, line_color=p["axis"])
    return _base_layout(fig, mode, max(320, 18 * len(rows) + 60),
                        x={"tickprefix": "$", "tickformat": "~s",
                           "title": "← ahead of quota · projected shortfall →"},
                        y={"showgrid": False})


def scenario_attainment(projection: pd.DataFrame, mode: str) -> go.Figure:
    """Baseline vs scenario projected attainment per segment, with a 100% quota reference."""
    p = PALETTES[mode]
    rows = projection[projection["rep_segment"] != "Total"]
    fig = go.Figure()
    for name, col, color in (("Baseline", "baseline_attainment", p["series"][0]),
                             ("Scenario", "scenario_attainment", p["series"][1])):
        fig.add_bar(
            name=name, x=rows["rep_segment"], y=rows[col], marker_color=color,
            customdata=rows[[col.replace("attainment", "projected"), "quarterly_quota"]],
            hovertemplate=(f"<b>{name}</b> · %{{x}}<br>Projected: $%{{customdata[0]:,.0f}}"
                           "<br>Quota: $%{customdata[1]:,.0f}<br>Attainment: %{y:.0%}<extra></extra>"),
        )
    fig.add_hline(y=1.0, line_width=1, line_color=p["ink_secondary"],
                  annotation_text="Quota", annotation_position="top left",
                  annotation_font=dict(color=p["ink_secondary"], size=12))
    return _base_layout(fig, mode, 340, y={"tickformat": ".0%", "title": "Projected attainment"},
                        x={"showgrid": False})
