-- For closed deals, the time spent in each stage must add up exactly to the full sales cycle.
select opportunity_id, sales_cycle_days,
    coalesce(days_in_prospecting, 0) + coalesce(days_in_discovery, 0)
    + coalesce(days_in_demo, 0) + coalesce(days_in_negotiation, 0) as summed_stage_days
from {{ ref('fct_pipeline_velocity') }}
where not is_open
  and sales_cycle_days <> summed_stage_days
