-- One row per logged sales activity.
select
    activity_id,
    opportunity_id,
    activity_type,
    activity_timestamp,
    cast(activity_timestamp as date) as activity_date
from {{ source('raw', 'activities') }}
