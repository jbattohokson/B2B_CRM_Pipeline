# Raw data (not tracked in git)

Dataset: [CRM + Sales + Opportunities](https://www.kaggle.com/datasets/innocentmfa/crm-sales-opportunities) (Kaggle, Mursaleen Ameer, Apache 2.0, ~8,800 opportunity rows). Chosen 2026-09-20 over the originally-planned generic 5-file schema because it has real opportunity-grain data (deal stage/size/probability in `sales_pipeline.csv`) needed for the Weighted Pipeline and Forecast Accuracy metrics — most other Kaggle "B2B CRM" datasets are company/contact segmentation data with no deal-stage information at all.

Log into Kaggle, download the dataset, and place these 5 files here exactly as named:

- `accounts.csv`
- `products.csv`
- `sales_pipeline.csv` — this is the opportunity-grain fact table (`fct_opportunities` will be built from this)
- `sales_teams.csv` — this is the rep/user dimension (`dim_users`)
- `data_dictionary.csv` — column definitions, not loaded into the model, kept for reference

Note: there's no separate `leads.csv` or `tasks.csv` in this dataset — `sales_pipeline.csv`'s stage column (starts at "Prospecting") covers the lead-to-opportunity flow in one table instead of two. The star schema in the README will be adjusted to match once these are inspected for real, rather than assuming the original 5-table guess.

These are gitignored (see root `.gitignore`) — only this README is tracked, so the folder exists in a fresh clone.
