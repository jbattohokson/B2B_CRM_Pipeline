# Raw data (generated, not tracked in git)

Everything in this folder is produced by `pipelines/generate_data.py`, a seeded synthetic generator for B2B SaaS CRM data. Running it again with the same seed gives the same rows, so the CSVs are gitignored instead of committed.

Expected files after generation:

- `dim_accounts.csv`: one row per customer account (industry, ARR, employee count, tier)
- `dim_reps.csv`: one row per sales rep (region, quota)
- `dim_date.csv`: calendar dimension
- `fct_opportunities.csv`: one row per opportunity (stage, amount, probability, created/close dates)
- `fct_stage_history.csv`: one row per opportunity per stage entered, with entry and exit dates (exit is blank for the current stage)
- `fct_activities.csv`: one row per sales activity logged against an opportunity

History: this project first planned to use a Kaggle dataset ([CRM + Sales + Opportunities](https://www.kaggle.com/datasets/innocentmfa/crm-sales-opportunities)). On 2026-10-19 it switched to synthetic data. The generator controls stage timestamps and activity logs directly, which the velocity, stalled-deal, and conversion metrics need and the Kaggle export doesn't have. It also lets the project run end to end on a fresh clone without a Kaggle login.
