-- Calendar dimension, one row per day.
select
    date_key,
    date_day,
    year,
    quarter,
    quarter_label,
    month,
    month_name,
    day_of_month,
    day_of_week,
    day_name,
    iso_week,
    is_weekend,
    first_day_of_month,
    first_day_of_quarter
from {{ source('raw', 'dates') }}
