# B2B Lead Funnel: Conversion, Speed & Rep Output (with a Local AI Analyst)

A dbt-duckdb star schema over **Olist's real B2B seller-acquisition funnel** (8,000 marketing-qualified leads, 842 signed deals). On top sits a Streamlit app with three parts: a funnel KPI dashboard, an "AI Funnel Copilot" that runs on a free local LLM (CrewAI + Ollama) and answers questions with SQL, and a lead mix simulator for planning next month's volume.

**Business question:** which lead sources turn MQLs into signed sellers, how fast do deals close, and how much lead volume (and from where) does a sales team need to hit a deal target? A sales manager can ask these in plain English and get an answer backed by SQL.

## Key findings

All figures come from the code in this repo, run on the committed data (as-of date 2018-11-14, the last close date in the data).

- **Overall conversion is 10.5%** (842 of 8,000 MQLs). The median deal closed **14 days** after first contact, and 66% of won deals (554) closed within 30 days. The mean is 48.5 days, pulled up by 2017 leads that waited months.
- **The sales team only started closing in December 2017.** 2017 leads converted at 3.1% with a median of 216 days to close. 2018 leads converted at 13.0% in a median of 12.5 days. Any conversion number that mixes the two periods understates current performance, so the dashboard has a "2018 leads" filter and the simulator uses 2018 rates.
- **Paid and organic search are the volume engines.** Paid search converts at 12.3% (95% CI 10.8-14.0%) and organic search at 11.8% (10.6-13.2%). Organic brings 29% of all MQLs.
- **Social and email underperform.** Social is 17% of MQL volume but converts at 5.6% (4.5-6.9%), half the average. Email converts at 3.0%. Both intervals sit fully below the paid and organic search intervals, so the gap isn't noise.
- **Leads with no recorded source convert best (16.7%).** That's 1,159 leads (1,099 tagged "unknown" plus 60 blank). It's a data-quality flag, not a channel: it could mean sales-sourced leads were never tagged. The dashboard shows it but the README doesn't recommend acting on it.
- **Rep output is concentrated.** One SDR (SDR 14) sourced 17% of won deals and one closer (Rep 08) closed 16%, across 32 SDRs and 22 closers.

## Architecture

1. **Fetch**: `pipelines/fetch_data.py` downloads the [Marketing Funnel by Olist](https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist) dataset from Kaggle and checks each CSV against a pinned SHA-256 hash. The two CSVs (875 KB) are also committed under `data/raw/` with their license (CC BY-NC-SA 4.0), so `--verify` checks them without a download.
2. **Ingest**: `pipelines/load_to_duckdb.py` checks every CSV header against a column/type contract, trims whitespace, and loads into the `raw` schema of `crm_analytics.duckdb`. Integrity checks run in the same transaction (primary keys, every deal's `mql_id` exists as a lead, accepted lead sources, unique sellers, no negative declared values). Any failure rolls back the whole load.
3. **Transform (dbt-duckdb)**: `models/staging/` cleans the two sources (missing source becomes `unknown`, a declared revenue of 0 becomes NULL). `models/marts/` builds:
   - `fct_lead_funnel`: one row per MQL with `is_won`, `days_to_close`, `won_within_{30,60,90,180}_days`, and `window_N_complete` flags so a young lead isn't counted as a 90-day miss before it's 90 days old.
   - `agg_lead_source_conversion`: conversion by source with a 95% Wilson interval (a dbt macro), share of MQLs and deals, and a conversion index vs the overall rate.
   - `agg_cohort_conversion`: conversion by month of first contact, overall and within 30/60/90/180 days (NULL until the whole cohort is old enough).
   - `dim_sdrs`, `dim_sales_reps`, `agg_segment_summary`, `agg_landing_page_conversion` (pages with 30+ MQLs), and `dim_date`.
   - 62 dbt checks pass, including 7 reconciliation tests in `data_tests/` (every MQL appears once, wins match the raw deals, source and cohort rollups add back to the fact table, window flags nest 30 ⊆ 60 ⊆ 90 ⊆ 180).
4. **Local AI agent** (`agent/`): a CrewAI "Senior Sales Development Analyst" on a local Ollama model (default `llama3.2`, 3B, which runs on an 8 GB laptop). There's no API key or cost, and nothing leaves the machine. It can be switched to Claude later through `.env`.
   - `schema_context.py` reads column lists live from DuckDB and adds a metric glossary and the as-of date, kept to about 1.3k tokens to fit Ollama's 4,096-token default context.
   - `sql_tool.py` is the `query_crm_database` tool. DuckDB's parser must see exactly one SELECT, the connection is read-only, and `enable_external_access=false` blocks reading local files. Results cap at 200 rows and queries time out after 15 s.
   - `fact_check.py` flags any number in the answer that doesn't appear in the query results. Small models invent filler figures, so the app shows flagged numbers next to the answer.
5. **App** (`app/`): Streamlit, three tabs, all read-only.
   - **Funnel KPIs**: conversion, median days to close, source conversion with intervals, cohort conversion and speed, SDR and closer output. Every chart has a table view.
   - **AI Funnel Copilot**: chat with the local agent, showing every SQL query and its result table.
   - **Lead Mix Simulator** (`simulator.py`): set monthly MQLs, move share between sources, and apply a conversion lift. It shows expected deals with a planning range from each source's interval, and the MQLs needed to hit a deal target. With no levers moved at the historical volume, it reproduces the 842 actual deals exactly (tested).

Requires Python 3.13 (dbt-duckdb has no 3.14 wheels yet).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python pipelines/fetch_data.py --verify   # or without --verify to re-download from Kaggle
.venv/bin/python pipelines/load_to_duckdb.py
DBT_PROFILES_DIR=. .venv/bin/dbt build
.venv/bin/pytest -q                                  # live-Ollama test is opt-in: RUN_OLLAMA_TESTS=1

ollama pull llama3.2
.venv/bin/python -m agent.crew_agent "Which lead source converts MQLs into deals best?"
.venv/bin/streamlit run app/main.py                  # close it before re-running the loader or dbt
```

## Tools

Python (pandas), DuckDB, dbt-duckdb, SQL, Streamlit, Plotly, CrewAI, Ollama (llama3.2), pytest, GitHub Actions.

## Data honesty

- **Real data, with real gaps.** The dataset has no deal amounts, stages, activity logs or quotas. So this project measures conversion and speed, not pipeline dollars or forecast attainment.
- **No per-rep conversion rates.** SDRs and closers are only recorded on won deals, so a rep's lost leads are invisible. Rep tables show volume and speed only, and the agent's prompt says so.
- **Declared revenue is too sparse to use.** Only 45 of 842 deals have a non-zero declared monthly revenue, so it isn't used in any metric.
- **One deal closed 2 days before its first contact date.** It's flagged (`is_close_before_first_contact`) and excluded from days-to-close, and a dbt test enforces that.
- **The May 2018 cohort is still maturing.** Its 180-day rate is NULL because some of its leads were under 180 days old at the as-of date.
- **History:** this repo started on seeded synthetic CRM data with stages, amounts and quotas. On 2026-10-19 it was rebuilt on Olist's real funnel. The earlier commits are kept as-is.

## Testing status (2026-10-20)

- **pytest:** 124 passed, 1 skipped (the opt-in live-Ollama test). Covers the fetch/hash check (no network), the loader (every validation rule tripped on an edited copy of the real CSVs, rollback, schema drift, a locked file), the SQL guard (writes, stacked statements and `read_csv()` refused), schema context, the agent wiring and retry logic with a fake crew, the figure checker, data access checked against direct SQL, the simulator math, all charts in light and dark mode, and a Streamlit `AppTest` run of the whole app (headline tiles show 8,000 / 842 / 10.5%, and moving the conversion slider changes expected deals).
- **dbt:** 62/62 pass on the real data.
- **Repeat runs:** the full CI sequence (verify → load → `dbt build` → pytest) was run 3 times from a deleted database with identical results.
- **Live agent:** the four `live_eval.py` questions were rewritten for the Olist schema but haven't been re-scored against a running Ollama model yet. The earlier synthetic-data eval (llama3.2: correct SQL and headline numbers 8/8, invented filler figures every run, caught by `fact_check.py`) is the reason the figure check exists.

**CI:** `.github/workflows/ci.yml` runs on every push and pull request: verify hashes → load → `dbt build` → `pytest -q`. It needs no secrets and no Ollama.

## License

Code: MIT (see `LICENSE`). Data in `data/raw/`: CC BY-NC-SA 4.0, by Olist.
