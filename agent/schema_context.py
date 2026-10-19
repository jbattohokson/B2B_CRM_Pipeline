"""Build the schema briefing the RevOps agent carries in its backstory.

Small local models guess table and column names when they aren't told them.
This module puts the real mart schema into the prompt:

- Column lists are read live from DuckDB's information_schema, so they can't
  drift from what dbt actually built.
- Each table gets a one-line purpose, and key metrics get their business
  definitions (METRIC_GLOSSARY). A test checks that every glossary column exists.
- The as-of date and stall threshold come from dbt_project.yml vars, the same
  values the models use.

The output stays compact on purpose (about 1.3k tokens). Ollama's default
context window is 4,096 tokens, and CrewAI's own scaffolding plus query results
share it. Ollama's context-shift silently drops the oldest tokens, which is
where the schema sits. So column types are omitted (names carry the meaning),
and the calendar table gets a one-line summary instead of its full column list.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agent.sql_tool import PROJECT_ROOT, open_read_only

MARTS_SCHEMA = "marts"

# Tables the agent should use, in the order it should consider them, each with its grain.
MART_TABLES: dict[str, str] = {
    "fct_pipeline_velocity": "one row per opportunity (deal); start here for deal-level questions",
    "dim_sales_performance": "one row per sales rep; pipeline, current-quarter forecast, TTM results",
    "agg_stage_conversion": "one row per account_tier x funnel stage, plus account_tier='All' rollup",
    "fct_stage_history": "one row per opportunity per stage entered",
    "fct_activities": "one row per sales activity (call, email, meeting, demo, proposal)",
    "dim_accounts": "one row per account",
    "dim_reps": "one row per sales rep (attributes only)",
    "dim_date": "calendar, one row per day",
}

# Tables described in one line rather than by listing every column.
SUMMARIZED_TABLES: dict[str, str] = {
    "dim_date": "key columns date_day, year, quarter, quarter_label (e.g. '2026-Q3'), month, first_day_of_quarter",
}

# (table, column) -> business definition. Only metrics whose meaning isn't obvious from the name.
METRIC_GLOSSARY: dict[tuple[str, str], str] = {
    ("fct_pipeline_velocity", "weighted_amount"): "amount * stage_probability (CRM default 10/20/40/70%) for open deals; 0 when closed",
    ("fct_pipeline_velocity", "historical_weighted_amount"): "amount * historical win rate from the deal's stage+tier; 0 when closed",
    ("fct_pipeline_velocity", "is_stalled"): "open AND stage_name='Negotiation' AND days_in_current_stage > stall threshold",
    ("fct_pipeline_velocity", "sales_cycle_days"): "created_date to close_date, closed deals only (NULL while open)",
    ("fct_pipeline_velocity", "close_date"): "actual close date if closed; rep's expected close date if open",
    ("fct_pipeline_velocity", "is_closing_this_quarter"): "open with expected close in the current quarter",
    ("fct_pipeline_velocity", "is_won_this_quarter"): "won between quarter start and the as-of date (QTD)",
    ("fct_pipeline_velocity", "days_since_last_activity"): "days from last logged activity to as-of date (open deals)",
    ("dim_sales_performance", "won_qtd"): "Closed-Won amount quarter-to-date",
    ("dim_sales_performance", "forecast_weighted_pipeline"): "sum of weighted_amount for deals closing this quarter",
    ("dim_sales_performance", "expected_revenue_gap"): "quarterly_quota - (won_qtd + forecast_weighted_pipeline); positive = shortfall",
    ("dim_sales_performance", "expected_revenue_gap_historical"): "same gap using historical win rates instead of CRM probabilities",
    ("dim_sales_performance", "projected_attainment"): "(won_qtd + forecast_weighted_pipeline) / quarterly_quota",
    ("dim_sales_performance", "forecast_pipeline_coverage"): "unweighted pipeline closing this quarter / remaining_quota",
    ("dim_sales_performance", "win_rate_ttm"): "won deals / closed deals, trailing 365 days (count-based)",
    ("dim_sales_performance", "quota_attainment_ttm"): "won_amount_ttm / annual_quota",
    ("agg_stage_conversion", "conversion_rate"): "advanced / (advanced + lost) for deals that left the stage",
    ("agg_stage_conversion", "historical_win_rate_from_stage"): "probability a deal in this stage eventually wins",
    ("agg_stage_conversion", "pct_of_created_reaching_stage"): "funnel: deals entering this stage / deals entering Prospecting",
}

def load_dbt_vars(project_file: Path = PROJECT_ROOT / "dbt_project.yml") -> dict:
    """The vars block from dbt_project.yml (as_of_date, stalled_days_threshold)."""
    with project_file.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh).get("vars", {})


def fetch_mart_columns(db_path: Path | str | None = None) -> dict[str, list[tuple[str, str]]]:
    """{table: [(column, type), ...]} for the marts schema, in column order."""
    con = open_read_only(db_path)
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? ORDER BY table_name, ordinal_position",
            [MARTS_SCHEMA],
        ).fetchall()
    finally:
        con.close()
    columns: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype in rows:
        columns.setdefault(table, []).append((column, dtype))
    return columns


def build_schema_context(db_path: Path | str | None = None) -> str:
    """The text block describing tables, columns, metric definitions and conventions."""
    columns = fetch_mart_columns(db_path)
    dbt_vars = load_dbt_vars()
    missing = [t for t in MART_TABLES if t not in columns]
    if missing:
        raise RuntimeError(f"Mart tables missing from the database: {missing}. Run `dbt build` first.")

    lines = ["TABLES (DuckDB, always schema-qualify as marts.<table>):"]
    for table, purpose in MART_TABLES.items():
        if table in SUMMARIZED_TABLES:
            lines.append(f"- marts.{table}: {purpose}; {SUMMARIZED_TABLES[table]}")
            continue
        cols = ", ".join(c for c, _ in columns[table])
        lines.append(f"- marts.{table}: {purpose}\n  columns: {cols}")

    lines.append("\nMETRIC DEFINITIONS:")
    for (table, column), definition in METRIC_GLOSSARY.items():
        lines.append(f"- {table}.{column}: {definition}")

    lines.append(
        "\nCONVENTIONS:\n"
        f"- 'Today' (as-of date) is {dbt_vars['as_of_date']}; the current quarter is the one containing it.\n"
        f"- Stall threshold: {dbt_vars['stalled_days_threshold']} days in Negotiation.\n"
        "- stage_name values: 'Prospecting','Discovery','Demo','Negotiation','Closed-Won','Closed-Lost'.\n"
        "- account_tier / rep_segment values: 'SMB','Mid-Market','Enterprise'. "
        "Regions: 'NA-East','NA-West','EMEA','APAC'.\n"
        "- 'Pipeline' or deal value means amount (unweighted). Use weighted_amount only for "
        "expected/weighted/forecast revenue.\n"
        "- Money columns are USD. Rates (win_rate, conversion_rate, probability) are 0-1 decimals.\n"
        "- Qualify columns as alias.column (FROM marts.fct_pipeline_velocity f ... f.rep_name), "
        "never alias.table.column.\n"
        "- Rep names live in fct_pipeline_velocity.rep_name and dim_sales_performance.rep_name; no join needed."
    )
    return "\n".join(lines)
