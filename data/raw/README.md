# Raw data: Olist Marketing Funnel (real, anonymized)

These two files are the real B2B seller-acquisition funnel of [Olist](https://olist.com), a Brazilian marketplace that signs up merchants to sell through it. Olist published them on Kaggle as the [Marketing Funnel by Olist](https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist) dataset. IDs are anonymized hashes.

| File | Rows | One row per |
|---|---|---|
| `olist_marketing_qualified_leads_dataset.csv` | 8,000 | marketing-qualified lead (MQL): first contact date, landing page, lead source |
| `olist_closed_deals_dataset.csv` | 842 | MQL that became a seller: SDR, sales rep, close date, business segment, lead type and a few self-declared attributes |

MQLs arrived between 2017-06-14 and 2018-05-31. Deals closed between 2017-12-05 and 2018-11-14.

**License:** [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). The files are redistributed here unchanged, with attribution to Olist, for non-commercial use.

**Integrity:** `pipelines/fetch_data.py` re-downloads them from Kaggle and checks these SHA-256 hashes:

```
5537753b7549ce4068e52e2402c29f2773c5375fc0575520ae8f3f301f666573  olist_marketing_qualified_leads_dataset.csv
1433f402d8fff00ca167579de641d9525f6e846368958c166f699ef1b0c31f3d  olist_closed_deals_dataset.csv
```

**What the data doesn't have:** deal stages, deal amounts, activity logs or quotas. Only won deals carry an SDR and sales rep, so lost leads can't be attributed to a rep. See the main README's Data honesty section for what that means for the metrics.

**History:** an earlier version of this project ran on seeded synthetic CRM data with stages, amounts and quotas. On 2026-10-19 it was rebuilt on this real funnel instead, trading stage-level velocity metrics for real conversion behavior.
