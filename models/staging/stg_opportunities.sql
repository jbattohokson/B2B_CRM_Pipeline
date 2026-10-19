-- One row per opportunity in its current state, with funnel position and status flags.
select
    opportunity_id,
    account_id,
    rep_id,
    created_date,
    -- For closed deals this is the actual close date; for open deals, the rep's expected close date.
    close_date,
    stage as stage_name,
    {{ stage_order('stage') }} as stage_order,
    stage_entered_date,
    amount,
    probability as stage_probability,
    stage in ('Closed-Won', 'Closed-Lost') as is_closed,
    stage = 'Closed-Won' as is_won,
    stage = 'Closed-Lost' as is_lost,
    stage not in ('Closed-Won', 'Closed-Lost') as is_open
from {{ source('raw', 'opportunities') }}
