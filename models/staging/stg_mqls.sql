-- One row per marketing-qualified lead. 60 leads have no recorded source; they join
-- the 1,099 the export already labels 'unknown', with a flag to tell them apart.
select
    mql_id,
    first_contact_date,
    cast(date_trunc('month', first_contact_date) as date) as cohort_month,
    landing_page_id,
    coalesce(origin, 'unknown') as lead_source,
    origin is null as is_source_missing
from {{ source('raw', 'mqls') }}
