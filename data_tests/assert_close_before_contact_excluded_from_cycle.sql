-- A deal dated before its lead's first contact is flagged and kept out of cycle-time stats.
select mql_id from {{ ref('fct_lead_funnel') }}
where is_close_before_first_contact and (days_to_close is not null or won_within_30_days)
