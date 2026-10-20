-- Closing within 30 days implies within 60, 90 and 180, and only won leads can close.
select mql_id from {{ ref('fct_lead_funnel') }}
where (won_within_30_days and not won_within_60_days)
   or (won_within_60_days and not won_within_90_days)
   or (won_within_90_days and not won_within_180_days)
   or (won_within_180_days and not is_won)
