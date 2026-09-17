# B2B CRM & Pipeline Velocity

Dimensional model + dbt pipeline over B2B CRM data (leads, accounts, opportunities, tasks, users), built to answer: **how healthy is the sales pipeline, and where does it leak?**

## Status

🚧 Scaffolding stage — data not yet loaded. See [`data/raw/README.md`](data/raw/README.md) for expected inputs.

## Planned architecture

- **Source**: Kaggle B2B CRM dataset (5 CSVs — leads, accounts, opportunities, tasks, users)
- **Warehouse**: DuckDB (local, file-based)
- **Transform**: dbt-duckdb — staging models → dimensional mart (`fct_opportunities` grain = one row per opportunity, plus `dim_accounts`, `dim_users`, `dim_leads`, `dim_date`)
- **Metrics**: Weighted Pipeline, Lead-to-Opportunity Conversion Velocity, Forecast Accuracy %
- **BI**: Tableau Public, connected directly to `fct_opportunities`

## Environment

Requires **Python 3.13** — `dbt-duckdb`'s dependency chain has no CPython 3.14 wheels yet (same constraint as the `MLB_Live_Agent` project).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd dbt && DBT_PROFILES_DIR=. ../.venv/bin/dbt debug
```

## Repo layout

```
data/raw/        source CSVs (gitignored — not redistributed)
data/processed/  exported tables for Tableau etc. (gitignored)
dbt/             dbt-duckdb project: staging + mart models
sql/             ad-hoc / sanity-check SQL
python/          load, test, and export scripts
tableau/         Tableau Public workbook(s)
```

Full write-up (business question, data honesty notes, testing status with dates) will replace this once the pipeline is built.
