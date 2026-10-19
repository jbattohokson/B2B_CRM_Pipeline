-- A stalled flag only makes sense on an open Negotiation deal past the threshold.
select opportunity_id
from {{ ref('fct_pipeline_velocity') }}
where is_stalled
  and not (is_open and stage_name = 'Negotiation' and days_in_current_stage > {{ var('stalled_days_threshold') }})
