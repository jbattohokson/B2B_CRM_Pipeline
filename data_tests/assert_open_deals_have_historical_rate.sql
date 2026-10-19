-- Every open deal must find a historical win rate for its tier and stage (no silent null weighting).
select opportunity_id, account_tier, stage_name
from {{ ref('fct_pipeline_velocity') }}
where is_open and historical_win_probability is null
