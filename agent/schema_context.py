"""Build the schema briefing the funnel analyst agent carries in its backstory.

Small local models guess table and column names when they aren't told them.
This module puts the real mart schema into the prompt:

- Column lists are read live from DuckDB's information_schema, so they can't
  drift from what dbt actually built.
- Each table gets a one-line purpose, and key metrics get their business
  definitions (METRIC_GLOSSARY). A test checks that every glossary column exists.
- The as-of date comes from dbt_project.yml vars, the same value the models use.

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
    "fct_lead_funnel": "one row per marketing-qualified lead (MQL), won or not; start here for lead-level questions",
    "agg_lead_source_conversion": "one row per lead_source: MQLs, deals won, conversion rate with 95% interval",
    "agg_cohort_conversion": "one row per cohort_month (month of first contact): conversion overall and within 30/60/90/180 days",
    "dim_sdrs": "one row per SDR (won deals only): volume and speed",
    "dim_sales_reps": "one row per sales rep / closer (won deals only): volume and speed",
    "agg_segment_summary": "one row per seller business_segment (won deals only)",
    "agg_landing_page_conversion": "one row per landing page with 30+ MQLs",
    "dim_date": "calendar, one row per day",
}

# Tables described in one line rather than by listing every column.
SUMMARIZED_TABLES: dict[str, str] = {
    "dim_date": "key columns date_day, year, quarter, quarter_label (e.g. '2018-Q1'), month, first_day_of_month",
}

# (table, column) -> business definition. Only metrics whose meaning isn't obvious from the name.
METRIC_GLOSSARY: dict[tuple[str, str], str] = {
    ("fct_lead_funnel", "is_won"): "the lead signed as a seller (closed-won); FALSE means lost or never closed",
    ("fct_lead_funnel", "days_to_close"): "first_contact_date to won_date in days; NULL if not won (and for 1 deal dated before first contact)",
    ("fct_lead_funnel", "won_within_90_days"): "won and closed within 90 days of first contact (also _30/_60/_180)",
    ("fct_lead_funnel", "window_90_complete"): "lead is at least 90 days old at the as-of date, so the 90-day flag is final",
    ("fct_lead_funnel", "sdr_id"): "SDR on the deal; NULL for leads that didn't close (the data only names reps on won deals)",
    ("agg_lead_source_conversion", "conversion_rate"): "deals_won / mqls for that lead source",
    ("agg_lead_source_conversion", "conversion_index"): "source conversion rate / overall conversion rate (1.0 = average)",
    ("agg_lead_source_conversion", "conversion_rate_ci_low"): "lower bound of the 95% Wilson interval (ci_high is the upper)",
    ("agg_cohort_conversion", "conversion_rate_90d"): "share of the cohort won within 90 days; NULL until the whole cohort is 90+ days old",
    ("dim_sdrs", "sdr_label"): "readable name like 'SDR 14' (IDs are anonymized hashes)",
    ("dim_sdrs", "share_closed_within_30_days"): "share of the SDR's won deals that closed within 30 days of first contact",
    ("dim_sales_reps", "sales_rep_label"): "readable name like 'Rep 07'",
}

def load_dbt_vars(project_file: Path = PROJECT_ROOT / "dbt_project.yml") -> dict:
    """The vars block from dbt_project.yml (as_of_date, min_landing_page_mqls)."""
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
        f"- 'Today' (as-of date) is {dbt_vars['as_of_date']}. Leads arrived 2017-06 to 2018-05.\n"
        "- Real anonymized data from Olist's seller-acquisition funnel. There are no deal amounts, stages or quotas.\n"
        "- Conversion = won leads / all leads. Per-SDR or per-rep conversion rates can't be computed: "
        "reps are only recorded on won deals.\n"
        "- lead_source values: 'organic_search','paid_search','social','unknown','direct_traffic','email',"
        "'referral','other','display','other_publicities'.\n"
        "- Use sdr_label / sales_rep_label (from dim_sdrs / dim_sales_reps) when naming reps; join on sdr_id / sr_id.\n"
        "- Rates are 0-1 decimals. Use median for days_to_close (it is heavily skewed).\n"
        "- Qualify columns as alias.column (FROM marts.fct_lead_funnel f ... f.lead_source), never alias.table.column."
    )
    return "\n".join(lines)
