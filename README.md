# B2B SaaS CRM Pipeline Velocity & Local AI Forecasting Agent

This project is a dbt-duckdb star schema over B2B SaaS CRM data. A Streamlit app sits on top with three parts: an executive KPI dashboard, a "Revenue Copilot" that runs on a local LLM (CrewAI + Ollama) and answers questions with SQL, and a pipeline risk simulator.

**Business question this answers:** where is the sales pipeline actually slowing down (by stage, tier, and rep), which deals have stalled, and how big is the gap between weighted pipeline and quota? A sales director can ask these questions in plain English and get a SQL-backed answer.

## Architecture

1. **Generate**: `pipelines/generate_data.py` writes six seeded synthetic CRM CSVs to `data/raw/`: accounts, reps, date, opportunities, stage history, and activities. Stage history (one row per opportunity per stage entered, with entry and exit dates) wasn't in the original table list. It's included because stage durations and stage-to-stage conversion can't be computed honestly from a "current stage" column alone.
2. **Ingest**: `pipelines/load_to_duckdb.py` checks every CSV header against an explicit column/type contract, trims whitespace, and loads the files into the `raw` schema of `crm_analytics.duckdb`. It then runs integrity checks in the same transaction (primary keys, foreign keys, accepted stage values, amount/probability ranges, date ordering, and opportunity stage vs. latest stage-history row). Any failure rolls back the whole load.
3. **Transform (dbt-duckdb)**: `models/staging/` holds six `stg_*` views that rename columns and add funnel-order and status flags. `models/marts/` builds the star schema in a `marts` schema:
   - `fct_pipeline_velocity`: one row per opportunity. Weighted amount (amount × CRM stage probability), a **historical weighted amount** (amount × the historical win rate from the deal's current stage and tier), sales cycle and deal age, days in each stage, days since last activity, an `is_stalled` flag (open in Negotiation > 45 days), and current-quarter flags (`is_won_this_quarter`, `is_closing_this_quarter`).
   - `dim_sales_performance`: one row per rep. Open and weighted pipeline, the current-quarter forecast, which is the standard in-quarter RevOps definition: **expected revenue gap** = quarterly quota − (won QTD + weighted pipeline closing this quarter), plus projected attainment. The gap is computed with both CRM weights and historical weights. Also pipeline coverage against remaining quota, stalled deals, TTM bookings, win rate, attainment, and average cycle.
   - `agg_stage_conversion`: stage-to-stage conversion and average/median days in stage, by tier plus an "All" rollup. Conversion counts resolved stage visits only (advanced ÷ (advanced + lost)), so deals still in a stage don't drag down recent cohorts. `historical_win_rate_from_stage` multiplies the conversion rates from a stage through Closed-Won.
   - `fct_stage_history`, `fct_activities`, `dim_accounts`, `dim_reps`, and `dim_date` complete the star.
   - The as-of date (2026-08-15, mid-Q3) and the 45-day stall threshold are dbt vars in `dbt_project.yml`. 72 dbt tests cover keys, relationships, accepted values/ranges, and seven reconciliation checks in `data_tests/`.
4. **Local AI agent** (`agent/`): a CrewAI "Senior RevOps Analyst" running on a local Ollama model (default `llama3.2`, 3B, which runs on an 8 GB laptop; set `OLLAMA_MODEL=llama3.1` on machines with 16 GB+ RAM). Nothing leaves the machine, and there's no API key or cost. CrewAI tracing and OpenTelemetry export are switched off before `crewai` is imported.
   - `schema_context.py` builds the agent's backstory. Column lists are read live from DuckDB, so they can't drift from what dbt built. A glossary of metric definitions comes with them (a test checks every glossary column still exists), and the as-of date and stall threshold come from the dbt vars. It's kept to about 1.3k tokens because Ollama's default context is 4,096 tokens, and context-shift silently drops the oldest tokens, which is where the schema sits.
   - `sql_tool.py` is the `query_crm_database` tool, with three layers of protection. DuckDB's own parser must see exactly one SELECT statement. The connection is `read_only`. And `enable_external_access=false` blocks `read_csv()`/`COPY`/`ATTACH` against local files. Results are capped at 200 rows, and a timer interrupts queries that run longer than 15 s. Errors go back to the model as text so it can fix its SQL.
   - `crew_agent.py` runs the crew. It checks Ollama is up and the model is pulled (with fix-it messages), logs every query with its DataFrame for the UI, and re-runs once if no query succeeded.
   - `fact_check.py` flags any figure in the answer that doesn't match a value in the query results (see Testing status for why).
   - `live_eval.py` asks four questions against live Ollama and prints each answer next to ground truth computed directly in SQL.
5. **App** (`app/`): a Streamlit app (`main.py`) with three tabs. All of it reads the warehouse through the same read-only, no-external-access connection the agent uses (`data.py`).
   - **Executive KPIs**: a segment filter scopes the whole tab. Tiles show open pipeline, won QTD, Q3 forecast attainment under CRM weights *and* under historical win rates, and stalled deals. Below them are the funnel (share of created deals reaching each stage, by tier), stage velocity (average days per stage, by tier), stalled pipeline by rep, and a diverging revenue-gap-by-rep chart with a CRM/historical toggle. Every chart has a table view.
   - **AI Revenue Copilot**: a chat over the local agent, with suggested questions and a model picker that lists the installed Ollama models. Each answer shows every SQL query with its result table, and any figures `fact_check.py` couldn't verify. Ungrounded answers are flagged.
   - **Pipeline Risk Simulator** (`simulator.py`): levers for win-rate change, sales-cycle change (stretching the time each open deal has left, which slips deals out of the quarter), CRM vs historical weighting, and writing off stalled negotiations. It shows baseline vs scenario projected bookings and attainment by segment, deals slipping out of the quarter, and the across-the-board win-rate lift needed to hit quota (solved by bisection). A zero-change scenario reproduces the dbt mart's forecast exactly.
   - `charts.py`: Plotly figures in one palette, checked with a palette validator in light and dark mode. Tiers use an ordinal one-hue ramp because they're ordered, baseline vs scenario use two categorical hues, and shortfall vs ahead use blue/red diverging poles. Each tier keeps its own color when a filter hides the others.
6. **Tests**: `tests/` has pytest coverage for the generator, the loader, the agent's SQL guardrails/schema context/wiring, the figure check, and the app (data access, simulator math, charts, and a Streamlit `AppTest` run of the whole app). The agent tests run against a warehouse that the test fixture builds with the real loader and `dbt build`. dbt tests live in `data_tests/` and the model YAMLs.

Requires Python 3.13. `dbt-duckdb`'s dependency chain has no CPython 3.14 wheels yet (same constraint as `../MLB_Live_Agent`).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Generate synthetic data (seed 42, as-of 2026-08-15) and load it into DuckDB
.venv/bin/python pipelines/generate_data.py
.venv/bin/python pipelines/load_to_duckdb.py

# Build and test the dbt models (staging views + mart tables)
DBT_PROFILES_DIR=. .venv/bin/dbt build

# Run the Python tests (the live-Ollama test is opt-in: RUN_OLLAMA_TESTS=1)
.venv/bin/pytest -q

# Ask the local agent a question (needs Ollama running: https://ollama.com).
# Default model is llama3.2 (3B). For llama3.1 (8B, 16 GB+ RAM): ollama pull llama3.1 && export OLLAMA_MODEL=llama3.1
ollama pull llama3.2
.venv/bin/python -m agent.crew_agent "Which reps have deals stuck in negotiation over 45 days?"

# Score the agent against SQL ground truth (takes minutes on a laptop CPU)
.venv/bin/python -m agent.live_eval

# Run the dashboard (http://localhost:8501). Close it before re-running the loader or dbt:
# they need the DuckDB write lock.
.venv/bin/streamlit run app/main.py
```

## Tools

Python (pandas), DuckDB, dbt-duckdb, SQL, Streamlit, Plotly, CrewAI, Ollama (llama3.2 / llama3.1), pytest.

## Data honesty

- All data is synthetic, from a seeded generator. Patterns such as tier-dependent cycle lengths and stalled negotiations are designed in, so the analysis shows that the pipeline and metrics work. It doesn't claim real-world findings.
- Generator assumptions (all constants live at the top of `generate_data.py`):
  - **Funnel:** Prospecting → Discovery → Demo → Negotiation → Closed-Won, with a chance of Closed-Lost at each step. Base advance rates are 65/60/55/60%, adjusted by tier (SMB +5 pts, Enterprise −5 pts) and by a hidden per-rep skill multiplier (0.85–1.15 per stage, compounding to roughly 0.5×–1.75× on overall win rate).
  - **Tiers:** set by employee count (SMB < 200, Mid-Market 200–1,999, Enterprise 2,000+). Mean days per stage and deal sizes scale with tier (median deal $12k / $45k / $150k).
  - **Stalls:** 15% of deals reaching Negotiation stall for 60–180 days. That rises to 20% for Enterprise, and is 2.5× higher for a hidden "staller" subset (about 15%) of reps. Stalled deals win half as often, and activity drops to a trickle after 30 days.
  - **Routing:** every deal goes to a random rep in the segment that matches the account's tier. Region is deliberately ignored (see Testing status for why).
  - **Quotas:** calibrated per segment (SMB $360–500k, Mid-Market $680–950k, Enterprise $850k–1.15M) so a typical rep lands near 80–90% attainment, given that segment's deal flow.
  - **ARR:** an account's ARR is the sum of its Closed-Won amounts from the trailing 365 days. This assumes 1-year contracts; renewals aren't modeled, so ARR is 0 for prospects and for lapsed customers.
  - **Dates:** the simulation's "today" is fixed at 2026-08-15 (mid-quarter, so there are both quarter-to-date bookings and in-quarter pipeline to forecast), so runs are reproducible. Open deals get a rep-entered expected close date, which is pushed into the future if the planned date has already passed.
  - **Volume:** the opportunity creation rate grows 2.5× across the window. With the defaults, trailing-12-month bookings come to 85% of total annual quota. Individual reps range from 9% to 170%.
- **The CRM stage probabilities (10/20/40/70%) don't match history, and that's by design.** Real CRMs ship with fixed defaults like these, while the historical win rates from each stage are 15 / 22 / 35 / 59%. Late stages are over-weighted, and that's where most weighted dollars sit. So for 2026-Q3, CRM weights project **117%** of quota ($2.51M won QTD + $4.64M weighted pipeline vs a $6.12M quota), while historical weights project **102%** ($3.71M weighted). That's about $0.9M of forecast optimism, and 18 vs 21 reps projected short. Both versions are in `dim_sales_performance`, so the gap between them is visible instead of buried.
- Rep skill and the staller flag are deliberately *not* written to the CSVs. The analysis has to find these effects in the outcomes, the same way it would in a real CRM.

## Testing status (2026-10-19)

Every layer is tested: generator, loader, dbt models, local agent, and Streamlit app.

- **pytest:** 47 tests, all passing (`tests/test_generate_data.py`, `tests/test_load_to_duckdb.py`), run on a small seeded config (1,500 opportunities, about 1 s to generate). Generator tests cover byte-identical output for the same seed, PK/FK integrity, valid funnel walks in stage history (in order, contiguous dates, exactly one open step), probability-by-stage, close-date rules for open vs. closed deals, activity timestamps inside deal lifetimes, the ARR rule, and that stalled Negotiation deals actually exist. Loader tests cover contract types, whitespace trimming, every validation rule tripping on a corrupted CSV with the exact row count reported, rollback leaving the previous load intact, missing/renamed columns, uncastable values, and a locked database file.
- **dbt:** `dbt build` passes 85/85: 13 models and 72 tests. Beyond generic tests, the singular tests reconcile the marts to each other: one velocity row per raw opportunity, stage days summing exactly to the sales cycle for every closed deal, stalled flags only on open Negotiation deals past the threshold, rep rollups (including QTD wins and historical-weighted pipeline) matching the opportunity fact, the conversion "All" row equaling the sum of the tiers, every open deal finding a historical win rate, and quarter flags respecting the as-of date. Negative control: re-running the stalled-flag test with the threshold var set to 90 (while the tables were built at 45) fails with exactly the 31 rows expected, so the test really does catch violations. (That control ran at the earlier 2026-09-30 as-of date.)
- **Repeat runs:** generate → load → `dbt build` from a deleted database. At the 2026-09-30 as-of date, 3 runs gave identical content hashes for all 14 tables (6 `raw`, 8 `marts`). After adding historical weighting, the 3× check **caught a real bug**: `dim_sales_performance` hashed differently between runs. `historical_weighted_amount` was a floating-point product, and DuckDB's parallel `sum()` adds doubles in nondeterministic order, which changed the last bits of rep totals. Fix: round it to cents as `DECIMAL(14,2)`, which makes sums exact. After the fix, 5 runs were identical.
- **Metric sanity (seed 42, as-of 2026-08-15):** 417 open deals, 296 of them expected to close this quarter, and 41 stalled. Stalled deals average 16.9 days since last activity, vs 2.9 for other open deals. Won-deal cycle is 50 / 72 / 108 days for SMB / Mid-Market / Enterprise. Negotiation's mean stay (36 days) is double its median (18 days), which is the stall long tail showing up.
- **Forecast definition fix (2026-10-19):** the first version used an as-of date of 2026-09-30 (the last day of Q3) and forecast the *next* quarter. Every open deal's close date falls after the as-of date, so about 97% of open pipeline landed in that quarter, and the forecast flag barely filtered anything. Moving the as-of date to mid-quarter and switching to won-QTD + in-quarter weighted pipeline fixed it.
- **Tuning found on the first generator run:** with the original 4,000 opportunities, only 169 deals were open as of 2026-09-30, and trailing-12-month bookings were about 37% of quota. That's too thin for the dashboard and an unrealistically large revenue gap. Raising volume to 9,000 and the growth factor to 2.5× fixed it.
- **Generator bug found by the first dbt run:** rep-level attainment in `dim_sales_performance` ranged from 7% to 216%. Two causes. Routing deals by segment *and* region gave a lone rep in a small region a whole region's share of a segment. And the per-stage skill multiplier (originally σ = 0.12, clipped 0.7–1.3) compounded across four stage transitions. The fix: routing is now segment-only, skill is σ = 0.06 clipped 0.85–1.15, and quotas were recalibrated per segment. The remaining spread (7%–203%) mostly comes from small samples: Enterprise reps close about 10 deals a year at an 8% win rate, so a rep with zero wins in a year is plausible.
- **Agent unit tests (no LLM needed):** the SQL guard rejects INSERT/UPDATE/DELETE/DROP/CREATE/ATTACH/COPY/SET and stacked statements. A `read_csv()` of a local file through a SELECT is refused. The 200-row cap and the 15 s timeout both work. Glossary columns are checked against the warehouse, and the schema prompt stays under budget. Ollama-down and model-missing errors are clear. The tool returns errors as text and logs queries. Retry-when-ungrounded and stop-after-max-attempts are covered (with a fake crew), and the figure checker is tested on real llama3.2 answers. Full suite: 144 passed, 1 skipped (the opt-in live test).
- **Live agent (llama3.2 3B, the default):** `agent/live_eval.py` ran 4 questions, twice.
  - **SQL and headline answers: 8/8 grounded and correct on the first attempt** (41 stalled deals / $1,373,500; Maya Brooks's $212,400 gap; 49% Enterprise Demo conversion; 50.4 / 71.9 / 108.4-day cycles). 44–108 s per question on a laptop CPU.
  - **The write-up around those answers is where the 3B model fabricates.** Run 1 invented a "$1,000,000 stall threshold," Mid-Market/SMB conversion rates and a "$250k deal size" it never queried, and an overall average it never computed. It also recommended "increasing" a rep's revenue gap. Tightening the prompt ("quote numbers exactly as they appear; never add a figure you did not query") cleaned up Q3, but run 2 still invented a "$1,736,000 quarterly quota" and "12.5%" shortfall percentages for Q2.
  - **Response:** `fact_check.py` flags those figures deterministically. It's tested on the actual answers above and catches every invented figure with no false positives once prompt constants (45-day threshold, 365-day TTM) are allowed. The Streamlit app will show flagged figures next to the answer instead of presenting the prose as fact.
  - **An earlier failure mode, fixed by retry:** one development run wrote invalid SQL (`alias.table.column`) and then printed the tool call as text instead of making it, so it returned an answer with no data behind it. Fixes: a schema tip on alias syntax, plus one automatic retry when no query succeeded.
  - **llama3.1 8B (the spec's original default) could not be evaluated on the dev machine** (Apple M2, 8 GB RAM). Loaded, the model takes 5.7 GB, and swap hit 14.5 of 15 GB. After 21 minutes, Q1 still had no answer, and one model call exceeded the 300 s request timeout. The eval was stopped and the model unloaded. This is a hardware limit, not a measured quality result: llama3.1's answer quality on this eval is still unknown.

  | Model | Machine | Grounded answers | Correct SQL + headline | Latency / question | Invented filler figures |
  |---|---|---|---|---|---|
  | llama3.2 3B | M2, 8 GB | 8/8 (2 runs) | 8/8 | 44–108 s | Yes, every run (flagged by `fact_check.py`) |
  | llama3.1 8B | M2, 8 GB | 0/1 finished | n/a | >21 min (stopped) | n/a |

  Because of this, the code default switched to llama3.2 on 2026-10-19; llama3.1 stays selectable via `OLLAMA_MODEL`. Re-run `agent.live_eval --model llama3.1` on a machine with 16 GB+ to fill in the second row.
- **App tests:** data access is checked against direct SQL. Segment totals add up to the overall totals. Quarter bounds are right for every quarter, including the Q4→January rollover. The simulator's zero-change scenario reconciles to `dim_sales_performance` (CRM basis to the cent; historical within $1, from per-deal cent rounding in the mart). Longer cycles never add bookings. A −100% win rate leaves only won-QTD. Write-off removes exactly the stalled in-quarter value. The solved win-rate lift reaches quota and 1 point less doesn't, and an unreachable quota returns "none" instead of a number. All charts build in both themes, and tier colors don't repaint under a filter. A `streamlit.testing` AppTest renders all three tabs against the fixture warehouse, moves the cycle slider to +100%, and checks that deals start slipping.
- **Browser check (Streamlit 1.66, light and dark, 800 px and 1400 px wide):** all three tabs render. With the copilot on llama3.2 (19 s with the model warm), the answer, the figure-check warning, and the SQL and result table all display. Bugs found and fixed in this pass:
  - **Clipped axis labels.** Rep and stage names were cut off because of a fixed 8 px margin. Fixed with Plotly automargin. While debugging, it turned out Streamlit re-runs `main.py` but doesn't reload an edited `app/charts.py`, so the server has to be restarted after changing helper modules.
  - **Truncated KPI tiles.** Five equal columns cut values ("$21…") at 800 px. Fixed with fixed-width tiles in a wrapping row.
  - **Off-by-one label.** "Ends Oct 01" now shows the last day, Sep 30.
  - **Misleading zero delta.** A green "↑ +$0" appeared when no lever was moved; it now reads "no change".
  - **Figure-check false positive.** The check flagged "21 reps," which was the result's row count. Row counts now count as verified figures.
  - **Bad test fixture.** The fixture's as-of date was the last day of a quarter, which left no in-quarter pipeline (the same trap as the original real config). It moved to mid-quarter.
- **Not testable:** whether the generator's distributions match any particular real company's CRM. There's no real data to compare against, so realism rests on the documented assumptions above.

## Repo hygiene note

`data/raw/*.csv` and `*.duckdb` are gitignored. Both are fully reproducible from `generate_data.py` (seeded) and the loader, so nothing is lost by excluding them. `.env` / `.streamlit/secrets.toml` are gitignored as a precaution, though the local Ollama setup needs no secrets.
