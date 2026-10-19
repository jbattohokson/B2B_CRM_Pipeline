-- Quarter flags must respect the as-of date: QTD wins on or before it, quarter pipeline after it.
select opportunity_id
from {{ ref('fct_pipeline_velocity') }}
where (is_won_this_quarter and (close_date > {{ as_of_date() }} or not is_won))
   or (is_closing_this_quarter and (close_date <= {{ as_of_date() }} or not is_open))
   or (is_won_this_quarter and is_closing_this_quarter)
