-- Calendar from the first lead to the as-of date.
with bounds as (
    select min(first_contact_date) as start_day from {{ ref('stg_mqls') }}
)

select
    cast(d as date) as date_day,
    year(d) as year,
    quarter(d) as quarter,
    year(d) || '-Q' || quarter(d) as quarter_label,
    month(d) as month,
    cast(date_trunc('month', d) as date) as first_day_of_month,
    dayofweek(d) as day_of_week,
    dayofweek(d) in (0, 6) as is_weekend
from bounds, range(bounds.start_day, {{ as_of_date() }} + interval 1 day, interval 1 day) t(d)
