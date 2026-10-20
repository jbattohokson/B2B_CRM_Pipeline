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

import os
from datetime import date
from pathlib import Path

import pandas as pd

from agent.schema_context import load_dbt_vars
from agent.sql_tool import PROJECT_ROOT, open_read_only

REQUIRED_MARTS = ("fct_pipeline_velocity", "dim_sales_performance", "agg_stage_conversion")
TIER_ORDER = ["SMB", "Mid-Market", "Enterprise"]
STAGE_ORDER = ["Prospecting", "Discovery", "Demo", "Negotiation"]


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


def current_quarter_bounds(as_of: date) -> tuple[date, date]:
    """(first day, first day of next quarter) for the quarter containing as_of."""
    q_start_month = 3 * ((as_of.month - 1) // 3) + 1
    start = date(as_of.year, q_start_month, 1)
    end = date(as_of.year + (q_start_month == 10), 1 if q_start_month == 10 else q_start_month + 3, 1)
    return start, end


def quarter_label(as_of: date) -> str:
    return f"{as_of.year}-Q{(as_of.month - 1) // 3 + 1}"


# ---------------------------------------------------------------------------
# Dashboard queries
# ---------------------------------------------------------------------------


def _segment_filter(column: str, segment: str | None) -> tuple[str, list]:
    if segment and segment != "All":
        return f"WHERE {column} = ?", [segment]
    return "", []


def load_kpis(db_path: Path | str, segment: str | None = None) -> dict:
    """Headline numbers for the KPI row, optionally for one rep segment."""
    where, params = _segment_filter("rep_segment", segment)
    row = query(
        db_path,
        f"""
        SELECT
            sum(open_deal_count) AS open_deals,
            sum(open_pipeline_amount) AS open_pipeline,
            sum(won_qtd) AS won_qtd,
            sum(quarterly_quota) AS quarterly_quota,
            sum(projected_bookings) AS projected_crm,
            sum(projected_bookings_historical) AS projected_historical,
            sum(stalled_deal_count) AS stalled_deals,
            sum(stalled_pipeline_amount) AS stalled_pipeline,
            count(*) FILTER (WHERE expected_revenue_gap > 0) AS reps_short,
            count(*) AS reps
        FROM marts.dim_sales_performance {where}
        """,
        params,
    ).iloc[0]
    kpis = {k: (float(v) if v is not None else 0.0) for k, v in row.items()}
    quota = kpis["quarterly_quota"] or 1.0
    kpis["attainment_crm"] = kpis["projected_crm"] / quota
    kpis["attainment_historical"] = kpis["projected_historical"] / quota
    return kpis


def load_funnel(db_path: Path | str, segment: str | None = None) -> pd.DataFrame:
    """Stage conversion by tier (excluding the 'All' rollup), in funnel order."""
    where, params = _segment_filter("account_tier", segment)
    where = where or "WHERE account_tier <> 'All'"
    df = query(
        db_path,
        f"""
        SELECT account_tier, stage_name, stage_order, deals_entered, conversion_rate,
               pct_of_created_reaching_stage, avg_days_in_stage, median_days_in_stage,
               historical_win_rate_from_stage
        FROM marts.agg_stage_conversion {where}
        ORDER BY stage_order
        """,
        params,
    )
    df["account_tier"] = pd.Categorical(df["account_tier"], TIER_ORDER, ordered=True)
    return df.sort_values(["account_tier", "stage_order"]).reset_index(drop=True)


def load_rep_performance(db_path: Path | str, segment: str | None = None) -> pd.DataFrame:
    where, params = _segment_filter("rep_segment", segment)
    return query(
        db_path,
        f"""
        SELECT rep_id, rep_name, rep_segment, rep_region, quarterly_quota, won_qtd,
               forecast_weighted_pipeline, forecast_weighted_pipeline_historical,
               expected_revenue_gap, expected_revenue_gap_historical, projected_attainment,
               stalled_deal_count, stalled_pipeline_amount, open_deal_count, open_pipeline_amount,
               win_rate_ttm, quota_attainment_ttm, avg_sales_cycle_days_won
        FROM marts.dim_sales_performance {where}
        ORDER BY rep_name
        """,
        params,
    )


def load_stalled_deals(db_path: Path | str, segment: str | None = None) -> pd.DataFrame:
    where, params = _segment_filter("a.account_tier", segment)
    where = (where + " AND" if where else "WHERE") + " f.is_stalled"
    return query(
        db_path,
        f"""
        SELECT f.opportunity_id, f.company_name, f.account_tier, f.rep_name, f.amount,
               f.days_in_current_stage, f.days_since_last_activity, f.close_date
        FROM marts.fct_pipeline_velocity f
        JOIN marts.dim_accounts a USING (account_id)
        {where}
        ORDER BY f.days_in_current_stage DESC
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Simulator inputs
# ---------------------------------------------------------------------------


def load_open_deals(db_path: Path | str) -> pd.DataFrame:
    """Every open deal with what the simulator needs to re-weight and re-time it."""
    return query(
        db_path,
        """
        SELECT f.opportunity_id, f.rep_id, r.rep_segment, f.account_tier, f.stage_name,
               f.amount::DOUBLE AS amount,
               f.stage_probability::DOUBLE AS crm_probability,
               f.historical_win_probability AS historical_probability,
               f.close_date, f.is_stalled
        FROM marts.fct_pipeline_velocity f
        JOIN marts.dim_reps r USING (rep_id)
        WHERE f.is_open
        """,
    )


def load_segment_targets(db_path: Path | str) -> pd.DataFrame:
    """Quarterly quota and won-QTD per rep segment."""
    return query(
        db_path,
        """
        SELECT rep_segment, sum(quarterly_quota) AS quarterly_quota, sum(won_qtd)::DOUBLE AS won_qtd
        FROM marts.dim_sales_performance
        GROUP BY rep_segment
        """,
    )
