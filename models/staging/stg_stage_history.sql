-- One row per opportunity per stage entered. exited_date is null for the stage the deal is in now.
select
    opportunity_id,
    stage_sequence,
    stage as stage_name,
    {{ stage_order('stage') }} as stage_order,
    entered_date,
    exited_date,
    exited_date is null as is_current_stage
from {{ source('raw', 'stage_history') }}
