# B2B CRM & Pipeline Velocity

A dimensional model + dbt pipeline over B2B CRM data (leads, accounts, opportunities, tasks, users), built to answer: how healthy is the sales pipeline, and where does it actually leak?

**Business question this answers:** which stage of the lead-to-revenue funnel is really slowing deals down, and how far off is the sales team's own forecast from what closes — turned into three concrete, queryable metrics (Weighted Pipeline, Lead-to-Opportunity Conversion Velocity, Forecast Accuracy %) instead of a gut-check.

## Architecture

1. **Source** — 5 CSVs (`leads`, `accounts`, `opportunities`, `tasks`, `users`) from a Kaggle B2B CRM dataset, dropped into `data/raw/` (not committed — see Repo hygiene note).
2. **Warehouse** — DuckDB, local and file-based (`dbt/b2b_crm_pipeline.duckdb`).
3. **Transform (dbt-duckdb)** — staging models clean each raw CSV 1:1; mart models build a star schema on top: `fct_opportunities` (grain = one row per opportunity) plus `dim_accounts`, `dim_users`, `dim_leads`, `dim_date`.
4. **Metrics** — Weighted Pipeline, Lead-to-Opportunity Conversion Velocity, and Forecast Accuracy % as SQL window functions/CTEs against `fct_opportunities`.
5. **BI** — Tableau Public, connected directly to the `fct_opportunities` export in `data/processed/`.

Requires Python 3.13 — `dbt-duckdb`'s dependency chain has no CPython 3.14 wheels yet (same constraint as `../MLB_Live_Agent`).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd dbt && DBT_PROFILES_DIR=. ../.venv/bin/dbt build
```

## Tools

Python (pandas), DuckDB, dbt-duckdb, SQL, Tableau Public.

## Data honesty

- This is a static, historical Kaggle export, not a live CRM feed — there's no live API to check the numbers against, so correctness relies on sanity checks (row counts, null rates, min/max dates) and deterministic repeat-runs rather than an external source of truth.
- *(Update once the specific Kaggle dataset is picked: known quirks, date range covered, any anonymized/synthetic fields.)*

## Testing status

Not applicable yet — no models exist. Once Step 4 (ETL) is built, this section records actual run results (row counts, null rates, 3x repeat-run diffs) and anything that couldn't be tested, following the same standard as the rest of this portfolio: run it for real, log what broke and what got fixed, don't fake a clean pass.

## Repo hygiene note

`data/raw/*.csv`, `data/processed/*`, and `dbt/*.duckdb` are gitignored — source data isn't redistributed and generated tables aren't committed. `data/raw/README.md` documents the exact 5 filenames expected, so the folder isn't empty on a fresh clone.
