"""Write the portfolio charts (PNG) and summary tables (CSV) from the built warehouse.

Every figure here is read from the dbt marts, so the charts can't drift from the
numbers in the README or the dashboard. Run after `dbt build`:

    .venv/bin/python reports/make_outputs.py            # uses crm_analytics.duckdb
    .venv/bin/python reports/make_outputs.py --db other.duckdb
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "outputs"

INK, MUTED, GRID = "#0f1117", "#4a4f5e", "#dde1ea"
ABOVE, BELOW, NEUTRAL = "#1a56db", "#c0392b", "#8b93a8"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
    "axes.titlesize": 12.5, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 12,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "savefig.dpi": 160, "savefig.bbox": "tight",
    "figure.facecolor": "white",
})


def q(con, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def save(fig, name: str) -> None:
    fig.savefig(OUT / name)
    plt.close(fig)
    print(f"  wrote outputs/{name}")


def source_conversion(con) -> pd.DataFrame:
    df = q(con, """
        SELECT lead_source, mqls, deals_won, conversion_rate, conversion_rate_ci_low AS ci_low,
               conversion_rate_ci_high AS ci_high, share_of_mqls, share_of_deals, conversion_index,
               median_days_to_close
        FROM marts.agg_lead_source_conversion ORDER BY conversion_rate""")
    overall = q(con, "SELECT avg(is_won::int) AS r FROM marts.fct_lead_funnel")["r"][0]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    colors = [ABOVE if r > overall else BELOW for r in df["conversion_rate"]]
    ax.barh(df["lead_source"], df["conversion_rate"], color=colors, height=0.62)
    ax.errorbar(df["conversion_rate"], df["lead_source"],
                xerr=[df["conversion_rate"] - df["ci_low"], df["ci_high"] - df["conversion_rate"]],
                fmt="none", ecolor=INK, elinewidth=1, capsize=3)
    ax.axvline(overall, color=INK, linestyle="--", linewidth=1)
    ax.text(overall, len(df) - 0.4, f" overall {overall:.1%}", color=INK, fontsize=9, va="bottom")
    for y, (r, n) in enumerate(zip(df["conversion_rate"], df["mqls"])):
        ax.text(max(df["ci_high"]) + 0.004, y, f"{r:.1%}  (n={n:,})", va="center", fontsize=8.5, color=MUTED)
    ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.set_xlim(0, max(df["ci_high"]) + 0.075)
    ax.grid(axis="y", visible=False)
    ax.set_title("MQL-to-deal conversion by lead source (95% Wilson interval)")
    ax.set_xlabel("Conversion rate")
    save(fig, "chart1_conversion_by_source.png")

    mix = df.sort_values("share_of_mqls")
    fig, ax = plt.subplots(figsize=(8, 4.8))
    y = range(len(mix))
    ax.barh([i + 0.2 for i in y], mix["share_of_mqls"], height=0.38, color=NEUTRAL, label="Share of MQLs")
    ax.barh([i - 0.2 for i in y], mix["share_of_deals"], height=0.38, color=ABOVE, label="Share of deals won")
    ax.set_yticks(list(y), mix["lead_source"])
    ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("Where leads come from vs where deals come from")
    save(fig, "chart2_mql_mix_vs_deal_mix.png")
    return df.sort_values("conversion_rate", ascending=False)


def cohorts(con) -> pd.DataFrame:
    df = q(con, """
        SELECT cohort_month, mqls, deals_won, conversion_rate_to_date, conversion_rate_30d,
               conversion_rate_90d, conversion_rate_180d, median_days_to_close
        FROM marts.agg_cohort_conversion WHERE mqls >= 30 ORDER BY cohort_month""")
    labels = pd.to_datetime(df["cohort_month"]).dt.strftime("%b\n%Y")
    fig, ax = plt.subplots(figsize=(9, 4.6))
    colors = [NEUTRAL if d.year == 2017 else ABOVE for d in pd.to_datetime(df["cohort_month"])]
    ax.bar(labels, df["conversion_rate_to_date"], color=colors, width=0.66, label="Converted to date")
    ax.plot(labels, df["conversion_rate_90d"], color=INK, marker="o", markersize=4, linewidth=1.4,
            label="Converted within 90 days (complete cohorts only)")
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("Conversion by month of first contact: the sales team ramps in Dec 2017")
    save(fig, "chart3_conversion_by_cohort.png")

    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.bar(labels, df["median_days_to_close"], color=colors, width=0.66)
    for x, v in zip(labels, df["median_days_to_close"]):
        if pd.notna(v):
            ax.text(x, v + 6, f"{v:.0f}", ha="center", fontsize=8.5, color=MUTED)
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("Median days, first contact to signed")
    ax.set_title("Speed to close by cohort (won deals only)")
    save(fig, "chart4_days_to_close_by_cohort.png")
    return df


def cycle_distribution(con) -> None:
    days = q(con, "SELECT days_to_close FROM marts.fct_lead_funnel WHERE days_to_close IS NOT NULL")["days_to_close"]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.hist(days.clip(upper=200), bins=40, color=ABOVE, edgecolor="white")
    med = days.median()
    ax.axvline(med, color=INK, linestyle="--", linewidth=1)
    ax.text(med + 3, ax.get_ylim()[1] * 0.9, f"median {med:.0f} days", fontsize=9, color=INK)
    within30 = (days <= 30).mean()
    ax.set_title(f"Days to close, won deals ({within30:.0%} close within 30 days; 200+ grouped at the right edge)")
    ax.set_xlabel("Days from first contact to signed")
    ax.set_ylabel("Deals")
    ax.grid(axis="x", visible=False)
    save(fig, "chart5_days_to_close_distribution.png")


def reps(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    sdrs = q(con, """SELECT sdr_label AS label, deals_won, share_of_deals, median_days_to_close,
                            share_closed_within_30_days, active_months, deals_per_active_month,
                            top_lead_source, top_business_segment
                     FROM marts.dim_sdrs ORDER BY deals_won DESC, label""")
    closers = q(con, """SELECT sales_rep_label AS label, deals_won, share_of_deals, median_days_to_close,
                               share_closed_within_30_days, active_months, deals_per_active_month,
                               top_lead_source, top_business_segment
                        FROM marts.dim_sales_reps ORDER BY deals_won DESC, label""")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.8))
    for ax, df, title in ((axes[0], sdrs, "SDRs (top 12 of %d)"), (axes[1], closers, "Closers (top 12 of %d)")):
        top = df.head(12).iloc[::-1]
        # top is reversed for plotting, so the leader is the last bar
        ax.barh(top["label"], top["deals_won"], color=[NEUTRAL] * (len(top) - 1) + [ABOVE], height=0.62)
        for y, v in enumerate(top["deals_won"]):
            ax.text(v + 1.5, y, str(v), va="center", fontsize=8.5, color=MUTED)
        ax.set_title(title % len(df), fontsize=11)
        ax.grid(axis="y", visible=False)
        ax.set_xlabel("Deals won")
    fig.suptitle("Output is concentrated: one SDR and one closer each handle about 1 in 6 deals",
                 x=0.01, ha="left", fontweight="bold", fontsize=12.5)
    fig.tight_layout()
    save(fig, "chart6_rep_output.png")
    return sdrs, closers


def segments(con) -> pd.DataFrame:
    df = q(con, """SELECT business_segment, deals_won, share_of_deals, median_days_to_close,
                          deals_with_declared_revenue
                   FROM marts.agg_segment_summary ORDER BY deals_won DESC""")
    top = df[df["business_segment"] != "unknown"].head(12).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.barh(top["business_segment"], top["deals_won"], color=ABOVE, height=0.62)
    for y, (v, d) in enumerate(zip(top["deals_won"], top["median_days_to_close"])):
        ax.text(v + 1.5, y, f"{v}  ·  median {d:.0f} days", va="center", fontsize=8.5, color=MUTED)
    ax.set_xlim(0, top["deals_won"].max() * 1.45)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Deals won")
    ax.set_title("Won deals by seller business segment (top 12)")
    save(fig, "chart7_segments.png")
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(PROJECT_ROOT / "crm_analytics.duckdb"))
    args = parser.parse_args(argv)
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect(args.db, read_only=True)
    try:
        tables = {
            "summary_by_lead_source.csv": source_conversion(con),
            "summary_by_cohort.csv": cohorts(con),
        }
        cycle_distribution(con)
        sdrs, closers = reps(con)
        tables["summary_by_sdr.csv"] = sdrs
        tables["summary_by_closer.csv"] = closers
        tables["summary_by_segment.csv"] = segments(con)
        tables["summary_by_landing_page.csv"] = q(
            con, "SELECT * FROM marts.agg_landing_page_conversion ORDER BY mqls DESC")
    finally:
        con.close()
    for name, df in tables.items():
        df.round({c: 4 for c in df.select_dtypes("number").columns}).to_csv(OUT / name, index=False)
        print(f"  wrote outputs/{name} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
