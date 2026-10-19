-- One row per sales activity, with the owning rep and account for easy slicing.
select
    a.activity_id,
    a.opportunity_id,
    o.account_id,
    o.rep_id,
    a.activity_type,
    a.activity_timestamp,
    a.activity_date
from {{ ref('stg_activities') }} a
join {{ ref('stg_opportunities') }} o using (opportunity_id)
