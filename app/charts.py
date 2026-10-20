"""Plotly figures for the dashboard, following one validated palette and mark spec.

Palette (validated with the dataviz skill's validate_palette.js on 2026-10-19,
light and dark, all checks passing):
- Single-series charts use the primary blue.
- Two-series categorical (baseline vs scenario, to-date vs 90-day): blue, orange.
- Above vs below the overall average: blue vs red poles.

Marks: thin bars with rounded data-ends, a small gap between bars, hairline
solid gridlines, no dual axes, and a hover tooltip on every mark. Every chart
in the app also has a table view.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

PALETTES = {
    "light": {
        "series": ["#2a78d6", "#eb6834"],
        "above": "#2a78d6",
        "below": "#e34948",
        "ink": "#0b0b0b",
        "ink_secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
    },
    "dark": {
        "series": ["#3987e5", "#d95926"],
        "above": "#3987e5",
        "below": "#e66767",
        "ink": "#f0efec",
        "ink_secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
    },
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def pct(value: float, digits: int = 1) -> str:
    """0.1053 -> '10.5%'."""
    return f"{value * 100:.{digits}f}%"


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


def source_conversion(sources: pd.DataFrame, overall_rate: float, mode: str) -> go.Figure:
    """Conversion by lead source with 95% intervals. Blue = above the overall rate, red = below."""
    p = PALETTES[mode]
    rows = sources.sort_values("conversion_rate")
    colors = [p["above"] if r >= overall_rate else p["below"] for r in rows["conversion_rate"]]
    fig = go.Figure(go.Bar(
        x=rows["conversion_rate"], y=rows["lead_source"], orientation="h", marker_color=colors,
        error_x=dict(type="data", symmetric=False, array=rows["ci_high"] - rows["conversion_rate"],
                     arrayminus=rows["conversion_rate"] - rows["ci_low"], color=p["ink_secondary"], thickness=1),
        customdata=rows[["mqls", "deals_won", "ci_low", "ci_high"]],
        hovertemplate=("<b>%{y}</b><br>Conversion: %{x:.1%} (95% CI %{customdata[2]:.1%}-%{customdata[3]:.1%})"
                       "<br>%{customdata[1]:,} deals from %{customdata[0]:,} MQLs<extra></extra>"),
    ))
    fig.add_vline(x=overall_rate, line_width=1, line_dash="dot", line_color=p["ink_secondary"],
                  annotation_text=f"overall {overall_rate:.1%}", annotation_position="top",
                  annotation_font=dict(color=p["ink_secondary"], size=12))
    return _base_layout(fig, mode, max(300, 30 * len(rows) + 60),
                        x={"tickformat": ".0%", "title": "MQL-to-deal conversion"}, y={"showgrid": False})


def cohort_conversion(cohorts: pd.DataFrame, mode: str) -> go.Figure:
    """Conversion by month of first contact: to date, and within 90 days where the window is complete."""
    p = PALETTES[mode]
    labels = pd.to_datetime(cohorts["cohort_month"]).dt.strftime("%b %Y")
    fig = go.Figure()
    for name, col, color in (("Converted to date", "conversion_rate_to_date", p["series"][0]),
                             ("Within 90 days", "conversion_rate_90d", p["series"][1])):
        fig.add_bar(
            name=name, x=labels, y=cohorts[col], marker_color=color,
            customdata=cohorts[["mqls", "deals_won"]],
            hovertemplate=(f"<b>{name}</b> · %{{x}}<br>%{{y:.1%}}<br>Cohort: %{{customdata[0]:,}} MQLs, "
                           "%{customdata[1]:,} deals to date<extra></extra>"),
        )
    return _base_layout(fig, mode, 330, y={"tickformat": ".0%", "title": "Share of cohort converted"},
                        x={"showgrid": False})


def cohort_cycle(cohorts: pd.DataFrame, mode: str) -> go.Figure:
    """Median days from first contact to close, by cohort (won deals only)."""
    p = PALETTES[mode]
    labels = pd.to_datetime(cohorts["cohort_month"]).dt.strftime("%b %Y")
    fig = go.Figure(go.Bar(
        x=labels, y=cohorts["median_days_to_close"], marker_color=p["series"][0],
        customdata=cohorts[["deals_won"]],
        hovertemplate="<b>%{x}</b><br>Median %{y:.0f} days to close<br>%{customdata[0]:,} deals<extra></extra>",
    ))
    return _base_layout(fig, mode, 330, y={"title": "Median days to close"}, x={"showgrid": False})


def rep_leaderboard(reps: pd.DataFrame, mode: str, top: int = 15) -> go.Figure:
    """Deals won per SDR or rep (top N), with speed in the tooltip."""
    p = PALETTES[mode]
    rows = reps.head(top).sort_values("deals_won")
    fig = go.Figure(go.Bar(
        x=rows["deals_won"], y=rows["label"], orientation="h", marker_color=p["series"][0],
        customdata=rows[["median_days_to_close", "share_closed_within_30_days", "top_lead_source"]],
        hovertemplate=("<b>%{y}</b><br>%{x:,} deals won<br>Median %{customdata[0]:.0f} days to close"
                       "<br>%{customdata[1]:.0%} closed within 30 days<br>Top source: %{customdata[2]}"
                       "<extra></extra>"),
    ))
    return _base_layout(fig, mode, max(280, 22 * len(rows) + 60),
                        x={"title": "Deals won"}, y={"showgrid": False})


def scenario_deals(projection: pd.DataFrame, mode: str) -> go.Figure:
    """Baseline vs scenario expected deals per month, by lead source."""
    p = PALETTES[mode]
    rows = projection[projection["lead_source"] != "Total"].sort_values("baseline_deals", ascending=False)
    fig = go.Figure()
    for name, col, color in (("Baseline", "baseline_deals", p["series"][0]),
                             ("Scenario", "scenario_deals", p["series"][1])):
        mql_col = "baseline_mqls" if name == "Baseline" else "scenario_mqls"
        fig.add_bar(
            name=name, x=rows["lead_source"], y=rows[col], marker_color=color,
            customdata=rows[[mql_col]],
            hovertemplate=(f"<b>{name}</b> · %{{x}}<br>%{{y:.1f}} expected deals/month"
                           "<br>from %{customdata[0]:.0f} MQLs<extra></extra>"),
        )
    return _base_layout(fig, mode, 340, y={"title": "Expected deals per month"}, x={"showgrid": False})
