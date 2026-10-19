# B2B SaaS CRM Pipeline Velocity & AI Forecasting Agent

This project is a dbt-duckdb star schema over B2B SaaS CRM data. A Streamlit app sits on top with three parts: an executive KPI dashboard, a Claude-powered text-to-SQL "Revenue Copilot", and a pipeline risk simulator.

**Business question this answers:** where is the sales pipeline actually slowing down (by stage, tier, and rep), which deals have stalled, and how big is the gap between weighted pipeline and quota? A sales director can ask these questions in plain English and get a SQL-backed answer.

## Architecture

1. **Generate**: `pipelines/generate_data.py` writes six seeded synthetic CRM CSVs to `data/raw/`: accounts, reps, date, opportunities, stage history, and activities. Stage history (one row per opportunity per stage entered, with entry and exit dates) wasn't in the original table list. It's included because stage durations and stage-to-stage conversion can't be computed honestly from a "current stage" column alone.
2. **Ingest**: `pipelines/load_to_duckdb.py` checks every CSV header against an explicit column/type contract, trims whitespace, and loads the files into the `raw` schema of `crm_analytics.duckdb`. It then runs integrity checks in the same transaction (primary keys, foreign keys, accepted stage values, amount/probability ranges, date ordering, and opportunity stage vs. latest stage-history row). Any failure rolls back the whole load.
3. **Transform (dbt-duckdb)**: `models/staging/` cleans types and names. `models/marts/` builds `fct_pipeline_velocity` and `dim_sales_performance` (sales cycle length, stage conversion, stalled-deal flags, expected revenue gap).
4. **AI agent**: `app/agent.py` gives Claude the schema and metric definitions, gets back read-only SQL, runs it against DuckDB, and summarizes the result.
5. **App**: `app/main.py` is a Streamlit app with three tabs: KPI Dashboard, AI Revenue Copilot, and Pipeline Risk Simulator.
6. **Tests**: `tests/` has pytest coverage for the generator, the loader, the SQL guardrails, and the metrics.

Requires Python 3.13. `dbt-duckdb`'s dependency chain has no CPython 3.14 wheels yet (same constraint as `../MLB_Live_Agent`).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Generate synthetic data (seed 42, as-of 2026-09-30) and load it into DuckDB
.venv/bin/python pipelines/generate_data.py
.venv/bin/python pipelines/load_to_duckdb.py

# Run the tests
.venv/bin/pytest -q
```

*(dbt and Streamlit run commands get added here as those stages are built.)*

## Tools

Python (pandas), DuckDB, dbt-duckdb, SQL, Streamlit, Plotly, Anthropic Claude API, pytest.

## Data honesty

- All data is synthetic, from a seeded generator. Patterns such as tier-dependent cycle lengths and stalled negotiations are designed in, so the analysis shows that the pipeline and metrics work. It doesn't claim real-world findings.
- Generator assumptions (all constants live at the top of `generate_data.py`):
  - **Funnel:** Prospecting → Discovery → Demo → Negotiation → Closed-Won, with a chance of Closed-Lost at each step. Base advance rates are 65/60/55/60%, adjusted by tier (SMB +5 pts, Enterprise −5 pts) and by a hidden per-rep skill multiplier.
  - **Tiers:** set by employee count (SMB < 200, Mid-Market 200–1,999, Enterprise 2,000+). Mean days per stage and deal sizes scale with tier (median deal $12k / $45k / $150k).
  - **Stalls:** 15% of deals reaching Negotiation stall for 60–180 days. That rises to 20% for Enterprise, and is 2.5× higher for a hidden "staller" subset (about 15%) of reps. Stalled deals win half as often, and activity drops to a trickle after 30 days.
  - **Routing:** every deal is worked by a rep whose segment matches the account's tier, in the account's region where possible.
  - **ARR:** an account's ARR is the sum of its Closed-Won amounts from the trailing 365 days. This assumes 1-year contracts; renewals aren't modeled, so ARR is 0 for prospects and for lapsed customers.
  - **Dates:** the simulation's "today" is fixed at 2026-09-30, so runs are reproducible. Open deals get a rep-entered expected close date, which is pushed into the future if the planned date has already passed.
  - **Volume:** the opportunity creation rate grows 2.5× across the window. With the defaults, trailing-12-month bookings come to about 85% of total annual quota, so the revenue-gap metric has a real gap to measure.
- Rep skill and the staller flag are deliberately *not* written to the CSVs. The analysis has to find these effects in the outcomes, the same way it would in a real CRM.

## Testing status (2026-10-19)

Generator and loader only. dbt, the agent, and the app don't exist yet.

- **pytest:** 47 tests, all passing (`tests/test_generate_data.py`, `tests/test_load_to_duckdb.py`), run on a small seeded config (1,500 opportunities, about 1 s to generate). Generator tests cover byte-identical output for the same seed, PK/FK integrity, valid funnel walks in stage history (in order, contiguous dates, exactly one open step), probability-by-stage, close-date rules for open vs. closed deals, activity timestamps inside deal lifetimes, the ARR rule, and that stalled Negotiation deals actually exist. Loader tests cover contract types, whitespace trimming, every validation rule tripping on a corrupted CSV with the exact row count reported, rollback leaving the previous load intact, missing/renamed columns, uncastable values, and a locked database file.
- **3× repeat run:** generate + load ran three times from scratch. CSV checksums and per-table content hashes of all six `raw` tables were identical across runs (600 accounts, 36 reps, 1,461 dates, 9,000 opportunities, 29,916 stage-history rows, 77,270 activities).
- **Tuning found on the first real run:** with the original 4,000 opportunities, only 169 deals were open as of 2026-09-30, and trailing-12-month bookings were about 37% of quota. That's too thin for the dashboard and an unrealistically large revenue gap. Raising volume to 9,000 and the growth factor to 2.5× gave 421 open deals, 47 Negotiation deals stalled more than 45 days, and about 85% attainment.
- **Not testable:** whether the generator's distributions match any particular real company's CRM. There's no real data to compare against, so realism rests on the documented assumptions above.

## Repo hygiene note

`data/raw/*.csv` and `*.duckdb` are gitignored. Both are fully reproducible from `generate_data.py` (seeded) and the loader, so nothing is lost by excluding them. `.env` / `.streamlit/secrets.toml` (the Claude API key) are gitignored too.
