-- One row per sales rep.
select
    rep_id,
    rep_name,
    region as rep_region,
    segment as rep_segment,
    annual_quota,
    annual_quota / 4.0 as quarterly_quota
from {{ source('raw', 'reps') }}
