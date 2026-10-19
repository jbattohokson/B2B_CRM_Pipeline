# B2B SaaS CRM Pipeline Velocity & AI Forecasting Agent

This project is a dbt-duckdb star schema over B2B SaaS CRM data. A Streamlit app sits on top with three parts: an executive KPI dashboard, a Claude-powered text-to-SQL "Revenue Copilot", and a pipeline risk simulator.

**Business question this answers:** where is the sales pipeline actually slowing down (by stage, tier, and rep), which deals have stalled, and how big is the gap between weighted pipeline and quota? A sales director can ask these questions in plain English and get a SQL-backed answer.

## Architecture

1. **Generate**: `pipelines/generate_data.py` writes seeded synthetic CRM CSVs (accounts, reps, date, opportunities, activities) to `data/raw/`.
2. **Ingest**: `pipelines/load_to_duckdb.py` loads the CSVs as raw tables into `crm_analytics.duckdb`.
3. **Transform (dbt-duckdb)**: `models/staging/` cleans types and names. `models/marts/` builds `fct_pipeline_velocity` and `dim_sales_performance` (sales cycle length, stage conversion, stalled-deal flags, expected revenue gap).
4. **AI agent**: `app/agent.py` gives Claude the schema and metric definitions, gets back read-only SQL, runs it against DuckDB, and summarizes the result.
5. **App**: `app/main.py` is a Streamlit app with three tabs: KPI Dashboard, AI Revenue Copilot, and Pipeline Risk Simulator.
6. **Tests**: `tests/` has pytest coverage for the generator, the loader, the SQL guardrails, and the metrics.

Requires Python 3.13. `dbt-duckdb`'s dependency chain has no CPython 3.14 wheels yet (same constraint as `../MLB_Live_Agent`).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

*(Run commands get added here as each pipeline stage is built.)*

## Tools

Python (pandas), DuckDB, dbt-duckdb, SQL, Streamlit, Plotly, Anthropic Claude API, pytest.

## Data honesty

- All data is synthetic, from a seeded generator. Patterns such as tier-dependent cycle lengths and stalled negotiations are designed in, so the analysis shows that the pipeline and metrics work. It doesn't claim real-world findings.
- *(Generator assumptions get documented here once `generate_data.py` exists.)*

## Testing status

Not applicable yet: only the scaffold exists. Once the pipeline runs, this section will record real run results (row counts, 3x repeat-run diffs, test results) and anything that couldn't be tested.

## Repo hygiene note

`data/raw/*.csv` and `*.duckdb` are gitignored. Both are fully reproducible from `generate_data.py` (seeded) and the loader, so nothing is lost by excluding them. `.env` / `.streamlit/secrets.toml` (the Claude API key) are gitignored too.
