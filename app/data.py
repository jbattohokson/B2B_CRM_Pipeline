"""Read-only data access for the Streamlit app.

Every query goes through agent.sql_tool.open_read_only, the same read-only,
no-external-access connection the AI agent uses. The app can therefore never
write to the warehouse, and it can run while you inspect the DuckDB file
elsewhere. A loader or dbt run still needs the write lock, so close the app
before rebuilding.

All functions take an explicit db_path, so tests can point them at a
fixture-built warehouse.
"""

from __future__ import annotations

import math
import os
from datetime import date
from pathlib import Path

import pandas as pd

from agent.schema_context import load_dbt_vars
from agent.sql_tool import PROJECT_ROOT, open_read_only

REQUIRED_MARTS = ("fct_lead_funnel", "agg_lead_source_conversion", "agg_cohort_conversion", "dim_sdrs",
                  "dim_sales_reps", "agg_segment_summary")

# Leads from before 2018 were mostly closed months later, once the sales team started working
# them (see the README). "2018" restricts every view to leads that arrived after that ramp.
PERIODS = {"All leads": None, "2018 leads": date(2018, 1, 1)}


def default_db_path() -> Path:
    """CRM_DB_PATH if set (read at call time, so tests can override it), else the project database."""
    return Path(os.getenv("CRM_DB_PATH", PROJECT_ROOT / "crm_analytics.duckdb"))


def query(db_path: Path | str, sql: str, params: list | None = None) -> pd.DataFrame:
    con = open_read_only(db_path)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def missing_marts(db_path: Path | str) -> list[str]:
    """Mart tables the app needs that aren't built yet (empty list = ready)."""
    built = set(
        query(db_path, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'")["table_name"]
    )
    return [t for t in REQUIRED_MARTS if t not in built]


def as_of_date() -> date:
    return date.fromisoformat(str(load_dbt_vars()["as_of_date"]))


def wilson_interval(successes: float, trials: float, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion. Same formula as the dbt wilson_bound macro."""
    if trials <= 0:
        return (0.0, 0.0)
    p = successes / trials
    centre = p + z * z / (2 * trials)
    spread = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    denom = 1 + z * z / trials
    return ((centre - spread) / denom, (centre + spread) / denom)


def _period_filter(period: str | None) -> tuple[str, list]:
    start = PERIODS.get(period or "All leads")
    if start is None:
        return "", []
    return "WHERE first_contact_date >= ?", [start]


# ---------------------------------------------------------------------------
# Dashboard queries
# ---------------------------------------------------------------------------


def load_kpis(db_path: Path | str, period: str | None = None) -> dict:
    """Headline numbers for the KPI row."""
    where, params = _period_filter(period)
    row = query(
        db_path,
        f"""
        SELECT
            count(*) AS mqls,
            count(*) FILTER (WHERE is_won) AS deals_won,
            median(days_to_close) AS median_days_to_close,
            count(*) FILTER (WHERE won_within_30_days) AS won_within_30_days,
            count(DISTINCT sdr_id) AS active_sdrs,
            count(DISTINCT sr_id) AS active_reps
        FROM marts.fct_lead_funnel {where}
        """,
        params,
    ).iloc[0]
    kpis = {k: (float(v) if v is not None and not pd.isna(v) else 0.0) for k, v in row.items()}
    kpis["conversion_rate"] = kpis["deals_won"] / kpis["mqls"] if kpis["mqls"] else 0.0
    kpis["share_won_within_30_days"] = kpis["won_within_30_days"] / kpis["deals_won"] if kpis["deals_won"] else 0.0
    return kpis


def load_source_conversion(db_path: Path | str, period: str | None = None) -> pd.DataFrame:
    """MQLs, deals and conversion (with 95% Wilson interval) per lead source, best first."""
    where, params = _period_filter(period)
    df = query(
        db_path,
        f"""
        SELECT lead_source, count(*) AS mqls, count(*) FILTER (WHERE is_won) AS deals_won,
               median(days_to_close) AS median_days_to_close
        FROM marts.fct_lead_funnel {where}
        GROUP BY lead_source
        """,
        params,
    )
    df["conversion_rate"] = df["deals_won"] / df["mqls"]
    bounds = [wilson_interval(w, n) for w, n in zip(df["deals_won"], df["mqls"])]
    df["ci_low"] = [b[0] for b in bounds]
    df["ci_high"] = [b[1] for b in bounds]
    df["share_of_mqls"] = df["mqls"] / df["mqls"].sum()
    return df.sort_values("conversion_rate", ascending=False).reset_index(drop=True)


def load_cohorts(db_path: Path | str) -> pd.DataFrame:
    return query(
        db_path,
        """
        SELECT cohort_month, mqls, deals_won, conversion_rate_to_date, conversion_rate_30d,
               conversion_rate_90d, conversion_rate_180d, median_days_to_close
        FROM marts.agg_cohort_conversion
        ORDER BY cohort_month
        """,
    )


def load_sdrs(db_path: Path | str) -> pd.DataFrame:
    return query(
        db_path,
        """
        SELECT sdr_label AS label, deals_won, share_of_deals, median_days_to_close,
               share_closed_within_30_days, deals_per_active_month, top_lead_source, top_business_segment
        FROM marts.dim_sdrs
        ORDER BY deals_won DESC, label
        """,
    )


def load_reps(db_path: Path | str) -> pd.DataFrame:
    return query(
        db_path,
        """
        SELECT sales_rep_label AS label, deals_won, share_of_deals, median_days_to_close,
               share_closed_within_30_days, deals_per_active_month, top_lead_source, top_business_segment
        FROM marts.dim_sales_reps
        ORDER BY deals_won DESC, label
        """,
    )


def load_segments(db_path: Path | str) -> pd.DataFrame:
    return query(
        db_path,
        """
        SELECT business_segment, deals_won, share_of_deals, median_days_to_close,
               deals_with_declared_revenue
        FROM marts.agg_segment_summary
        ORDER BY deals_won DESC
        """,
    )
